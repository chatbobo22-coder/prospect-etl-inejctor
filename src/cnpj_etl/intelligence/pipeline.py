"""Orquestra fontes uma por vez e mantém um perfil comercial consolidado."""

from __future__ import annotations

import hashlib
import logging
import time

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import IntelligenceSettings, SourceResult
from .scoring import calculate_profile
from .sources import collect_source

log = logging.getLogger(__name__)
INTELLIGENCE_LOCK = 7_262_603_882


def run_intelligence(conn, settings: IntelligenceSettings | None = None, *, force: bool = False) -> dict:
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
        totals["sources"] = per_source
        return totals
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (INTELLIGENCE_LOCK,))
        conn.commit()


def run_intelligence_until_empty(
    conn, settings: IntelligenceSettings | None = None, *, force: bool = False
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
    return totals


def _run_source(conn, source_code: str, settings: IntelligenceSettings, *, force: bool) -> dict:
    run_id = conn.execute(
        "INSERT INTO intelligence.source_runs (source_code) VALUES (%s) RETURNING id",
        (source_code,),
    ).fetchone()[0]
    conn.commit()
    stats = {"processed": 0, "success": 0, "no_data": 0, "failed": 0, "skipped": 0}
    try:
        companies = _pending_companies(conn, source_code, settings.batch_size, force=force)
        log.info("Inteligência %s: %s empresas", source_code, len(companies))
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
            except Exception as exc:
                conn.rollback()
                _mark_failed(conn, cnpj, source_code, exc)
                conn.commit()
                stats["processed"] += 1
                stats["failed"] += 1
                log.warning("Fonte %s falhou para %s: %s", source_code, cnpj, exc)
            time.sleep(settings.delay_seconds)
        conn.execute(
            """
            UPDATE intelligence.source_runs SET finished_at=now(), status='success',
              processed=%s, success=%s, no_data=%s, failed=%s
            WHERE id=%s
            """,
            (stats["processed"], stats["success"], stats["no_data"] + stats["skipped"], stats["failed"], run_id),
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


def _pending_companies(conn, source_code: str, limit: int, *, force: bool) -> list[dict]:
    where = "TRUE" if force else "(s.cnpj IS NULL OR s.next_check_at IS NULL OR s.next_check_at <= now())"
    query = f"""
        SELECT v.cnpj, v.cnpj_basico, v.razao_social, v.nome_fantasia,
               v.capital_social, v.porte, v.data_inicio_atividade, v.email,
               v.uf, v.municipio_descricao, v.cnae_fiscal_principal,
               d.email_dominio, d.site_url, d.site_final_url, d.site_valid,
               d.lead_score AS digital_lead_score, d.confidence_score AS digital_confidence_score,
               d.commerce_maturity, d.presence_maturity, d.has_chat, d.has_contact_form,
               d.has_checkout, d.has_product_page, d.whatsapp_valid,
               d.google_place_id, d.google_places_checked_at, d.google_rating,
               d.google_rating_count, d.google_maps_url
        FROM cnpj.v_prospect_candidates v
        LEFT JOIN cnpj.digital_presenca d ON d.cnpj=v.cnpj
        LEFT JOIN intelligence.company_source_state s
          ON s.cnpj=v.cnpj AND s.source_code=%s
        WHERE {where}
        ORDER BY v.cnpj
        LIMIT %s
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, (source_code, limit))
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
          next_check_at=now()+interval '1 day', updated_at=now()
        WHERE cnpj=%s AND source_code=%s
        """,
        (str(exc)[:2000], cnpj, source_code),
    )


def _persist_result(conn, cnpj: str, result: SourceResult) -> None:
    ttl = conn.execute(
        "SELECT ttl_days FROM intelligence.source_registry WHERE source_code=%s",
        (result.source_code,),
    ).fetchone()[0]
    conn.execute(
        "UPDATE intelligence.company_people SET active=false,updated_at=now() WHERE cnpj=%s AND source_code=%s",
        (cnpj, result.source_code),
    )
    for person in result.people:
        conn.execute(
            """
            INSERT INTO intelligence.company_people
              (cnpj,full_name,role_title,relationship_type,linkedin_url,business_email,
               business_phone,is_decision_maker,confidence,source_code,source_url,raw_data,active)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
            ON CONFLICT (cnpj,source_code,lower(full_name),lower(COALESCE(role_title,'')))
            DO UPDATE SET linkedin_url=EXCLUDED.linkedin_url,
              business_email=EXCLUDED.business_email,business_phone=EXCLUDED.business_phone,
              relationship_type=EXCLUDED.relationship_type,
              is_decision_maker=EXCLUDED.is_decision_maker,confidence=EXCLUDED.confidence,
              source_url=EXCLUDED.source_url,source_observed_at=now(),raw_data=EXCLUDED.raw_data,
              active=true,updated_at=now()
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
            ),
        )
    conn.execute(
        "DELETE FROM intelligence.company_signals WHERE cnpj=%s AND source_code=%s",
        (cnpj, result.source_code),
    )
    for signal in result.signals:
        fingerprint = _fingerprint(result.source_code, signal.signal_type, signal.title, signal.source_url)
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
          next_check_at=now()+(%s || ' days')::interval, last_error=NULL,
          metadata=%s,updated_at=now() WHERE cnpj=%s AND source_code=%s
        """,
        (result.status, records, ttl, Jsonb(result.metadata), cnpj, result.source_code),
    )


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
    conn.execute(
        """
        INSERT INTO intelligence.company_profiles
          (cnpj,fit_score,capacity_score,intent_score,pain_score,data_confidence_score,
           profile_score,profile_quality,estimated_capacity_band,intent_last_seen_at,
           decision_makers_count,signals_count,sources_success,sources_pending,summary,reasons,
           calculated_at,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
        ON CONFLICT (cnpj) DO UPDATE SET
          fit_score=EXCLUDED.fit_score,capacity_score=EXCLUDED.capacity_score,
          intent_score=EXCLUDED.intent_score,pain_score=EXCLUDED.pain_score,
          data_confidence_score=EXCLUDED.data_confidence_score,
          profile_score=EXCLUDED.profile_score,profile_quality=EXCLUDED.profile_quality,
          estimated_capacity_band=EXCLUDED.estimated_capacity_band,
          intent_last_seen_at=EXCLUDED.intent_last_seen_at,
          decision_makers_count=EXCLUDED.decision_makers_count,
          signals_count=EXCLUDED.signals_count,sources_success=EXCLUDED.sources_success,
          sources_pending=EXCLUDED.sources_pending,summary=EXCLUDED.summary,
          reasons=EXCLUDED.reasons,calculated_at=now(),updated_at=now()
        """,
        (
            cnpj,
            profile["fit_score"],
            profile["capacity_score"],
            profile["intent_score"],
            profile["pain_score"],
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
        ),
    )
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
            "SELECT * FROM intelligence.company_people WHERE cnpj=%s AND active=true ORDER BY is_decision_maker DESC,confidence DESC,full_name",
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
    return {"company": company, "people": people, "signals": signals, "sources": sources}


def _fingerprint(*parts: str | None) -> str:
    value = "|".join((part or "").strip().casefold() for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
