"""Orquestra fontes uma por vez e mantém um perfil comercial consolidado."""

from __future__ import annotations

from datetime import timedelta
import hashlib
import logging
import time
from collections.abc import Callable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import IntelligenceSettings, SourceResult
from .scoring import calculate_profile
from .sources import collect_source

log = logging.getLogger(__name__)
INTELLIGENCE_LOCK = 7_262_603_882


def run_intelligence(
    conn, settings: IntelligenceSettings | None = None, *, force: bool = False
) -> dict:
    settings = settings or IntelligenceSettings()
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (INTELLIGENCE_LOCK,)).fetchone()[0]:
        return {"locked": 1, "processed": 0}
    totals = {"processed": 0, "success": 0, "no_data": 0, "failed": 0, "skipped": 0}
    per_source: dict[str, dict] = {}
    try:
        available = {item["source_code"]: item for item in list_sources(conn)}
        unknown = sorted(set(settings.sources) - set(available))
        if unknown:
            raise ValueError(f"Fontes não registradas: {', '.join(unknown)}")
        for source_code in settings.sources:
            stats = _run_source(conn, source_code, settings, force=force)
            per_source[source_code] = stats
            for key in totals:
                totals[key] += stats.get(key, 0)
        refresh_company_groups(conn)
        totals["sources"] = per_source
        return totals
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (INTELLIGENCE_LOCK,))
        conn.commit()


def run_intelligence_until_empty(
    conn,
    settings: IntelligenceSettings | None = None,
    *,
    force: bool = False,
    after_round: Callable[[int, dict], None] | None = None,
) -> dict:
    settings = settings or IntelligenceSettings()
    totals = {"processed": 0, "success": 0, "no_data": 0, "failed": 0, "skipped": 0, "rounds": 0}
    for round_number in range(settings.max_rounds):
        stats = run_intelligence(conn, settings, force=force and round_number == 0)
        totals["rounds"] += 1
        for key in ("processed", "success", "no_data", "failed", "skipped"):
            totals[key] += stats.get(key, 0)
        if stats.get("processed", 0) == 0:
            break
        if after_round:
            after_round(round_number + 1, stats)
        if any(
            source_stats.get("circuit_open")
            for source_stats in stats.get("sources", {}).values()
        ):
            totals["circuit_open"] = 1
            break
    return totals


def _run_source(conn, source_code: str, settings: IntelligenceSettings, *, force: bool) -> dict:
    run_id = conn.execute(
        "INSERT INTO intelligence.source_runs (source_code) VALUES (%s) RETURNING id",
        (source_code,),
    ).fetchone()[0]
    conn.commit()
    stats = {
        "processed": 0,
        "success": 0,
        "no_data": 0,
        "failed": 0,
        "skipped": 0,
        "circuit_open": 0,
    }
    try:
        batch_size = (
            min(settings.batch_size, settings.gdelt_batch_size)
            if source_code == "gdelt"
            else min(settings.batch_size, settings.provider_batch_size)
            if source_code in {"apollo", "prospeo", "hunter"}
            else settings.batch_size
        )
        companies = _pending_companies(
            conn,
            source_code,
            batch_size,
            force=force,
            min_lead_score=settings.min_lead_score,
        )
        log.info("Inteligência %s: %s empresas", source_code, len(companies))
        consecutive_errors = 0
        for company in companies:
            cnpj = company["cnpj"]
            _mark_running(conn, cnpj, source_code)
            try:
                result = collect_source(source_code, conn, company, settings)
                _persist_result(conn, cnpj, result)
                refresh_profile(conn, cnpj)
                conn.commit()
                stats["processed"] += 1
                stats[result.status] = stats.get(result.status, 0) + 1
                transient = result.status == "skipped" and result.metadata.get("reason") in {
                    "rate_limited",
                    "temporarily_unavailable",
                }
                consecutive_errors = consecutive_errors + 1 if transient else 0
            except Exception as exc:
                conn.rollback()
                _mark_failed(conn, cnpj, source_code, exc)
                conn.commit()
                stats["processed"] += 1
                stats["failed"] += 1
                consecutive_errors += 1
                log.warning("Fonte %s falhou para %s: %s", source_code, cnpj, exc)
            delay_seconds = (
                max(settings.delay_seconds, settings.gdelt_delay_seconds)
                if source_code == "gdelt"
                else settings.delay_seconds
            )
            time.sleep(delay_seconds)
            if (
                source_code == "gdelt"
                and consecutive_errors >= settings.gdelt_circuit_breaker_errors
            ):
                stats["circuit_open"] = 1
                log.warning(
                    "Circuit breaker do GDELT aberto após %s erros consecutivos; "
                    "restante ficará para a próxima execução",
                    consecutive_errors,
                )
                break
        conn.execute(
            """
            UPDATE intelligence.source_runs SET finished_at=now(), status='success',
              processed=%s, success=%s, no_data=%s, failed=%s
            WHERE id=%s
            """,
            (
                stats["processed"],
                stats["success"],
                stats["no_data"] + stats["skipped"],
                stats["failed"],
                run_id,
            ),
        )
        conn.commit()
        return stats
    except Exception as exc:
        conn.rollback()
        conn.execute(
            "UPDATE intelligence.source_runs SET finished_at=now(),status='failed',error_message=%s WHERE id=%s",
            (str(exc)[:2000], run_id),
        )
        conn.commit()
        raise


def _pending_companies(
    conn,
    source_code: str,
    limit: int,
    *,
    force: bool,
    min_lead_score: int = 70,
) -> list[dict]:
    where = (
        "TRUE"
        if force
        else "(s.cnpj IS NULL OR s.next_check_at IS NULL OR s.next_check_at <= now())"
    )
    quality_gate = """
        AND EXISTS (
          SELECT 1
          FROM cnpj.prospectos_qualificados prospect
          WHERE prospect.cnpj=v.cnpj
            AND prospect.qualification_status='qualified'
            AND prospect.lead_quality IN ('A','B')
        )
    """ if source_code == "gdelt" else ""
    query = f"""
        SELECT v.cnpj, v.cnpj_basico, v.razao_social, v.nome_fantasia,
               v.capital_social, v.porte, v.data_inicio_atividade, v.email,
               v.uf, v.municipio_descricao, v.cnae_fiscal_principal,
               d.email_dominio, d.site_url, d.site_final_url, d.site_valid,
               d.lead_score AS digital_lead_score, d.confidence_score AS digital_confidence_score,
               d.commerce_maturity, d.presence_maturity, d.has_chat, d.has_contact_form,
               d.has_checkout, d.has_product_page, d.whatsapp_valid,
               d.plataforma, d.plataformas_detectadas, d.chat_provider, d.email_tipo,
               d.linkedin_url,
               d.google_place_id, d.google_places_checked_at, d.google_rating,
               d.google_rating_count, d.google_maps_url
        FROM cnpj.v_prospect_candidates v
        LEFT JOIN cnpj.digital_presenca d ON d.cnpj=v.cnpj
        LEFT JOIN intelligence.company_source_state s
          ON s.cnpj=v.cnpj AND s.source_code=%s
        WHERE {where}
        AND COALESCE(d.lead_score,0) >= %s
        {quality_gate}
        ORDER BY
          COALESCE(d.lead_score,0) DESC,
          COALESCE(d.confidence_score,0) DESC,
          v.cnpj
        LIMIT %s
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, (source_code, min_lead_score, limit))
        return list(cur.fetchall())


def _mark_running(conn, cnpj: str, source_code: str) -> None:
    conn.execute(
        """
        INSERT INTO intelligence.company_source_state
          (cnpj,source_code,status,attempts,last_checked_at,updated_at)
        VALUES (%s,%s,'running',1,now(),now())
        ON CONFLICT (cnpj,source_code) DO UPDATE SET
          status='running', attempts=intelligence.company_source_state.attempts+1,
          last_checked_at=now(), last_error=NULL, updated_at=now()
        """,
        (cnpj, source_code),
    )
    conn.commit()


def _mark_failed(conn, cnpj: str, source_code: str, exc: Exception) -> None:
    conn.execute(
        """
        UPDATE intelligence.company_source_state SET status='failed', last_error=%s,
          next_check_at=now()+CASE
            WHEN source_code='gdelt' AND attempts<=1 THEN interval '1 hour'
            WHEN source_code='gdelt' AND attempts=2 THEN interval '6 hours'
            ELSE interval '1 day'
          END,
          updated_at=now()
        WHERE cnpj=%s AND source_code=%s
        """,
        (str(exc)[:2000], cnpj, source_code),
    )


def _persist_result(conn, cnpj: str, result: SourceResult) -> None:
    ttl = conn.execute(
        "SELECT ttl_days FROM intelligence.source_registry WHERE source_code=%s",
        (result.source_code,),
    ).fetchone()[0]
    attempts = conn.execute(
        "SELECT attempts FROM intelligence.company_source_state "
        "WHERE cnpj=%s AND source_code=%s",
        (cnpj, result.source_code),
    ).fetchone()[0]
    transient_skip = result.status == "skipped" and result.metadata.get("reason") in {
        "rate_limited",
        "temporarily_unavailable",
    }
    retry_hours = 1 if attempts <= 1 else 6 if attempts == 2 else 24
    next_check_delay = (
        timedelta(hours=retry_hours) if transient_skip else timedelta(days=ttl)
    )
    conn.execute(
        "UPDATE intelligence.company_people SET active=false,updated_at=now() WHERE cnpj=%s AND source_code=%s",
        (cnpj, result.source_code),
    )
    for person in result.people:
        priority_score, priority_reason = _person_priority(person)
        conn.execute(
            """
            INSERT INTO intelligence.company_people
              (cnpj,full_name,role_title,relationship_type,linkedin_url,business_email,
               business_phone,is_decision_maker,confidence,source_code,source_url,raw_data,active,
               priority_score,priority_reason)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s)
            ON CONFLICT (cnpj,source_code,lower(full_name),lower(COALESCE(role_title,'')))
            DO UPDATE SET linkedin_url=EXCLUDED.linkedin_url,
              business_email=EXCLUDED.business_email,business_phone=EXCLUDED.business_phone,
              relationship_type=EXCLUDED.relationship_type,
              is_decision_maker=EXCLUDED.is_decision_maker,confidence=EXCLUDED.confidence,
              source_url=EXCLUDED.source_url,source_observed_at=now(),raw_data=EXCLUDED.raw_data,
              active=true,priority_score=EXCLUDED.priority_score,
              priority_reason=EXCLUDED.priority_reason,updated_at=now()
            """,
            (
                cnpj,
                person.full_name,
                person.role_title,
                person.relationship_type,
                person.linkedin_url,
                person.business_email,
                person.business_phone,
                person.is_decision_maker,
                person.confidence,
                result.source_code,
                person.source_url,
                Jsonb(person.raw_data),
                priority_score,
                priority_reason,
            ),
        )
    conn.execute(
        "DELETE FROM intelligence.company_signals WHERE cnpj=%s AND source_code=%s",
        (cnpj, result.source_code),
    )
    for signal in result.signals:
        fingerprint = _fingerprint(
            result.source_code, signal.signal_type, signal.title, signal.source_url
        )
        conn.execute(
            """
            INSERT INTO intelligence.company_signals
              (cnpj,source_code,signal_type,category,title,description,score,confidence,
               observed_at,expires_at,source_url,fingerprint,raw_data)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,now()),%s,%s,%s,%s)
            """,
            (
                cnpj,
                result.source_code,
                signal.signal_type,
                signal.category,
                signal.title,
                signal.description,
                signal.score,
                signal.confidence,
                signal.observed_at,
                signal.expires_at,
                signal.source_url,
                fingerprint,
                Jsonb(signal.raw_data),
            ),
        )
    records = len(result.people) + len(result.signals)
    conn.execute(
        """
        UPDATE intelligence.company_source_state SET status=%s, records_found=%s,
          next_check_at=now()+%s,
          last_error=NULL,
          metadata=%s,updated_at=now() WHERE cnpj=%s AND source_code=%s
        """,
        (
            result.status,
            records,
            next_check_delay,
            Jsonb(result.metadata),
            cnpj,
            result.source_code,
        ),
    )
    if result.source_code == "email_quality":
        _persist_email_verification(conn, cnpj, result.metadata.get("verification") or {}, ttl)
    if result.source_code == "website":
        _persist_technologies(conn, cnpj, result.metadata.get("technologies") or [], result)


def refresh_profile(conn, cnpj: str) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM intelligence.company_signals WHERE cnpj=%s",
            (cnpj,),
        )
        signals = list(cur.fetchall())
        cur.execute(
            "SELECT * FROM intelligence.company_people WHERE cnpj=%s AND active=true",
            (cnpj,),
        )
        people = list(cur.fetchall())
        cur.execute(
            "SELECT * FROM intelligence.company_source_state WHERE cnpj=%s",
            (cnpj,),
        )
        states = list(cur.fetchall())
    profile = calculate_profile(signals, people, states)
    feedback = _feedback_summary(conn, cnpj)
    profile.update(
        commercial_temperature=feedback["temperature"],
        last_commercial_event_at=feedback["last_event_at"],
        feedback_events_count=feedback["count"],
    )
    conn.execute(
        """
        INSERT INTO intelligence.company_profiles
          (cnpj,fit_score,capacity_score,intent_score,pain_score,presence_score,data_confidence_score,
           profile_score,profile_quality,estimated_capacity_band,intent_last_seen_at,
           decision_makers_count,signals_count,sources_success,sources_pending,summary,reasons,
           calculated_at,updated_at,commercial_temperature,last_commercial_event_at,
           feedback_events_count)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now(),%s,%s,%s)
        ON CONFLICT (cnpj) DO UPDATE SET
          fit_score=EXCLUDED.fit_score,capacity_score=EXCLUDED.capacity_score,
          intent_score=EXCLUDED.intent_score,pain_score=EXCLUDED.pain_score,
          presence_score=EXCLUDED.presence_score,
          data_confidence_score=EXCLUDED.data_confidence_score,
          profile_score=EXCLUDED.profile_score,profile_quality=EXCLUDED.profile_quality,
          estimated_capacity_band=EXCLUDED.estimated_capacity_band,
          intent_last_seen_at=EXCLUDED.intent_last_seen_at,
          decision_makers_count=EXCLUDED.decision_makers_count,
          signals_count=EXCLUDED.signals_count,sources_success=EXCLUDED.sources_success,
          sources_pending=EXCLUDED.sources_pending,summary=EXCLUDED.summary,
          reasons=EXCLUDED.reasons,calculated_at=now(),updated_at=now(),
          commercial_temperature=EXCLUDED.commercial_temperature,
          last_commercial_event_at=EXCLUDED.last_commercial_event_at,
          feedback_events_count=EXCLUDED.feedback_events_count
        """,
        (
            cnpj,
            profile["fit_score"],
            profile["capacity_score"],
            profile["intent_score"],
            profile["pain_score"],
            profile["presence_score"],
            profile["data_confidence_score"],
            profile["profile_score"],
            profile["profile_quality"],
            profile["estimated_capacity_band"],
            profile["intent_last_seen_at"],
            profile["decision_makers_count"],
            profile["signals_count"],
            profile["sources_success"],
            profile["sources_pending"],
            profile["summary"],
            profile["reasons"],
            feedback["temperature"],
            feedback["last_event_at"],
            feedback["count"],
        ),
    )
    # A camada genérica permanece intacta; o perfil Tironi é uma projeção
    # comercial adicional, recalculada incrementalmente a cada nova evidência.
    from ..intent.service import refresh_tironi_profile

    refresh_tironi_profile(conn, cnpj)
    return profile


def list_sources(conn) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.*,
              count(s.cnpj) FILTER (WHERE s.status='success') AS companies_success,
              count(s.cnpj) FILTER (WHERE s.status='failed') AS companies_failed,
              count(s.cnpj) FILTER (WHERE s.status='no_data') AS companies_no_data,
              max(s.last_checked_at) AS last_checked_at
            FROM intelligence.source_registry r
            LEFT JOIN intelligence.company_source_state s ON s.source_code=r.source_code
            GROUP BY r.source_code ORDER BY r.category,r.display_name
            """
        )
        return list(cur.fetchall())


def get_company_profile(conn, cnpj: str) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM intelligence.v_commercial_profiles WHERE cnpj=%s", (cnpj,))
        company = cur.fetchone()
        if not company:
            return None
        cur.execute(
            "SELECT * FROM intelligence.company_people WHERE cnpj=%s AND active=true ORDER BY priority_score DESC,confidence DESC,full_name",
            (cnpj,),
        )
        people = list(cur.fetchall())
        cur.execute(
            "SELECT * FROM intelligence.company_signals WHERE cnpj=%s AND (expires_at IS NULL OR expires_at>now()) ORDER BY category,score DESC",
            (cnpj,),
        )
        signals = list(cur.fetchall())
        cur.execute(
            "SELECT * FROM intelligence.company_source_state WHERE cnpj=%s ORDER BY source_code",
            (cnpj,),
        )
        sources = list(cur.fetchall())
        cur.execute(
            "SELECT * FROM intelligence.company_technologies WHERE cnpj=%s AND active=true ORDER BY category,confidence DESC,technology",
            (cnpj,),
        )
        technologies = list(cur.fetchall())
        cur.execute(
            "SELECT * FROM intelligence.commercial_feedback WHERE cnpj=%s ORDER BY occurred_at DESC LIMIT 100",
            (cnpj,),
        )
        feedback = list(cur.fetchall())
        cur.execute(
            "SELECT * FROM intelligence.company_group_members WHERE cnpj=%s",
            (cnpj,),
        )
        group = cur.fetchone()
    return {
        "company": company,
        "people": people,
        "signals": signals,
        "sources": sources,
        "technologies": technologies,
        "feedback": feedback,
        "group": group,
    }


def record_feedback(conn, payload: dict) -> dict:
    row = conn.execute(
        """
        INSERT INTO intelligence.commercial_feedback
          (cnpj,person_id,outcome,channel,campaign_id,source,external_id,notes,metadata,occurred_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,now()))
        ON CONFLICT (source,external_id) WHERE external_id IS NOT NULL DO UPDATE SET
          outcome=EXCLUDED.outcome,channel=EXCLUDED.channel,campaign_id=EXCLUDED.campaign_id,
          notes=EXCLUDED.notes,metadata=EXCLUDED.metadata,occurred_at=EXCLUDED.occurred_at
        RETURNING id,occurred_at
        """,
        (
            payload["cnpj"],
            payload.get("person_id"),
            payload["outcome"],
            payload.get("channel"),
            payload.get("campaign_id"),
            payload.get("source") or "mestrelead",
            payload.get("external_id"),
            payload.get("notes"),
            Jsonb(payload.get("metadata") or {}),
            payload.get("occurred_at"),
        ),
    ).fetchone()
    _sync_feedback_signals(conn, payload["cnpj"])
    profile = refresh_profile(conn, payload["cnpj"])
    conn.commit()
    return {"id": row[0], "occurred_at": row[1], "profile": profile}


def refresh_company_groups(conn) -> int:
    conn.execute("DELETE FROM intelligence.company_group_members")
    conn.execute("DELETE FROM intelligence.company_groups")
    conn.execute(
        """
        WITH candidates AS (
          SELECT v.cnpj,v.cnpj_basico,d.email_dominio,d.email_tipo,
            COALESCE(d.lead_score,0) AS lead_score,
            CASE
              WHEN d.email_tipo='corporativo' AND d.email_dominio IS NOT NULL
                THEN 'domain:' || lower(d.email_dominio)
              ELSE 'legal:' || v.cnpj_basico
            END AS group_key
          FROM cnpj.v_prospect_candidates v
          LEFT JOIN cnpj.digital_presenca d ON d.cnpj=v.cnpj
        ), ranked AS (
          SELECT *,row_number() OVER (PARTITION BY group_key ORDER BY lead_score DESC,cnpj) AS rn,
            count(*) OVER (PARTITION BY group_key) AS members_count
          FROM candidates
        )
        INSERT INTO intelligence.company_groups
          (group_key,root_domain,cnpj_basico,primary_cnpj,members_count)
        SELECT group_key,
          max(email_dominio) FILTER (WHERE email_tipo='corporativo'),
          max(cnpj_basico) FILTER (WHERE group_key LIKE 'legal:%'),
          max(cnpj) FILTER (WHERE rn=1),max(members_count)
        FROM ranked GROUP BY group_key
        """
    )
    result = conn.execute(
        """
        WITH candidates AS (
          SELECT v.cnpj,
            CASE
              WHEN d.email_tipo='corporativo' AND d.email_dominio IS NOT NULL
                THEN 'domain:' || lower(d.email_dominio)
              ELSE 'legal:' || v.cnpj_basico
            END AS group_key
          FROM cnpj.v_prospect_candidates v
          LEFT JOIN cnpj.digital_presenca d ON d.cnpj=v.cnpj
        )
        INSERT INTO intelligence.company_group_members
          (cnpj,group_key,is_primary,confidence,reason)
        SELECT c.cnpj,g.group_key,c.cnpj=g.primary_cnpj,
          CASE WHEN g.root_domain IS NOT NULL THEN 90 ELSE 100 END,
          CASE WHEN g.root_domain IS NOT NULL THEN 'corporate_domain' ELSE 'cnpj_basico' END
        FROM intelligence.company_groups g
        JOIN candidates c ON c.group_key=g.group_key
        """
    )
    conn.commit()
    return result.rowcount


def _persist_email_verification(conn, cnpj: str, item: dict, ttl: int) -> None:
    if not item.get("email"):
        return
    conn.execute(
        """
        INSERT INTO intelligence.email_verifications
          (cnpj,email,domain,syntax_valid,mx_valid,mx_hosts,disposable,email_role,
           deliverability_status,risk_score,reason_codes,checked_at,expires_at,last_error)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now()+(%s||' days')::interval,%s)
        ON CONFLICT (cnpj) DO UPDATE SET email=EXCLUDED.email,domain=EXCLUDED.domain,
          syntax_valid=EXCLUDED.syntax_valid,mx_valid=EXCLUDED.mx_valid,
          mx_hosts=EXCLUDED.mx_hosts,disposable=EXCLUDED.disposable,
          email_role=EXCLUDED.email_role,deliverability_status=EXCLUDED.deliverability_status,
          risk_score=EXCLUDED.risk_score,reason_codes=EXCLUDED.reason_codes,
          checked_at=now(),expires_at=EXCLUDED.expires_at,last_error=EXCLUDED.last_error,updated_at=now()
        """,
        (
            cnpj,
            item["email"],
            item.get("domain"),
            item["syntax_valid"],
            item.get("mx_valid"),
            item.get("mx_hosts") or [],
            item.get("disposable", False),
            item.get("email_role"),
            item["deliverability_status"],
            item.get("risk_score", 0),
            item.get("reason_codes") or [],
            ttl,
            item.get("error"),
        ),
    )


def _persist_technologies(conn, cnpj: str, technologies: list[dict], result: SourceResult) -> None:
    conn.execute(
        "UPDATE intelligence.company_technologies SET active=false,updated_at=now() WHERE cnpj=%s AND source_code=%s",
        (cnpj, result.source_code),
    )
    for item in technologies:
        conn.execute(
            """
            INSERT INTO intelligence.company_technologies
              (cnpj,technology,category,confidence,source_code,source_url,active,raw_data)
            VALUES (%s,%s,%s,%s,%s,%s,true,%s)
            ON CONFLICT (cnpj,technology,source_code) DO UPDATE SET
              category=EXCLUDED.category,confidence=EXCLUDED.confidence,
              source_url=EXCLUDED.source_url,observed_at=now(),active=true,
              raw_data=EXCLUDED.raw_data,updated_at=now()
            """,
            (
                cnpj,
                item["name"],
                item["category"],
                item["confidence"],
                result.source_code,
                item.get("source_url"),
                Jsonb(item.get("raw_data") or {}),
            ),
        )


def _person_priority(person) -> tuple[int, str]:
    relationship = person.relationship_type
    base = {
        "founder": 100,
        "administrator": 95,
        "executive": 90,
        "partner": 85,
        "contact": 55,
        "employee": 40,
    }.get(relationship, 30)
    if person.is_decision_maker:
        base = max(base, 90)
    return min(100, base), f"relationship:{relationship}"


def _feedback_summary(conn, cnpj: str) -> dict:
    row = conn.execute(
        """
        SELECT count(*),max(occurred_at),
          bool_or(outcome IN ('meeting_scheduled','opportunity_created','won')),
          bool_or(outcome IN ('replied_positive','opened','clicked')),
          bool_or(outcome IN ('bounced','unsubscribed','wrong_contact','replied_negative'))
        FROM intelligence.commercial_feedback WHERE cnpj=%s
        """,
        (cnpj,),
    ).fetchone()
    temperature = "hot" if row[2] else "warm" if row[3] else "cold" if row[4] else "uncontacted"
    return {"count": row[0], "last_event_at": row[1], "temperature": temperature}


def _sync_feedback_signals(conn, cnpj: str) -> None:
    conn.execute(
        "DELETE FROM intelligence.company_signals WHERE cnpj=%s AND source_code='commercial_feedback'",
        (cnpj,),
    )
    rows = conn.execute(
        "SELECT outcome,count(*),max(occurred_at) FROM intelligence.commercial_feedback WHERE cnpj=%s GROUP BY outcome",
        (cnpj,),
    ).fetchall()
    weights = {
        "opened": ("intent", 2),
        "clicked": ("intent", 4),
        "replied_positive": ("intent", 10),
        "meeting_scheduled": ("intent", 15),
        "opportunity_created": ("intent", 20),
        "won": ("intent", 25),
        "replied_negative": ("risk", 8),
        "wrong_contact": ("risk", 10),
        "bounced": ("risk", 20),
        "unsubscribed": ("risk", 25),
        "lost": ("risk", 10),
    }
    for outcome, count, observed_at in rows:
        if outcome not in weights:
            continue
        category, score = weights[outcome]
        conn.execute(
            """
            INSERT INTO intelligence.company_signals
              (cnpj,source_code,signal_type,category,title,score,confidence,observed_at,
               expires_at,fingerprint,raw_data)
            VALUES (%s,'commercial_feedback',%s,%s,%s,%s,100,%s,%s,%s,%s)
            """,
            (
                cnpj,
                outcome,
                category,
                f"Feedback comercial: {outcome}",
                min(25, score + max(0, count - 1)),
                observed_at,
                observed_at + timedelta(days=180),
                _fingerprint("commercial_feedback", outcome),
                Jsonb({"count": count}),
            ),
        )


def _fingerprint(*parts: str | None) -> str:
    value = "|".join((part or "").strip().casefold() for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
