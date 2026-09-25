"""Caminho quente: publica e-mails aproveitáveis sem aguardar crawling web."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import logging
import os

from psycopg.rows import dict_row

from .intelligence.email_quality import verify_email
from .outreach_sync import sync_qualified_leads

log = logging.getLogger(__name__)

FREE_DOMAINS = (
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "yahoo.com",
    "yahoo.com.br",
    "icloud.com",
    "live.com",
    "bol.com.br",
    "uol.com.br",
    "terra.com.br",
)

PRE_SCORE_SQL = """
LEAST(95,
  25
  + CASE WHEN lower(split_part(v.email,'@',2)) = ANY(%s) THEN 5 ELSE 15 END
  + CASE WHEN NULLIF(btrim(v.telefone_1),'') IS NOT NULL THEN 10 ELSE 0 END
  + CASE WHEN NULLIF(btrim(v.nome_fantasia),'') IS NOT NULL THEN 5 ELSE 0 END
  + CASE btrim(COALESCE(v.porte,'')) WHEN '05' THEN 10 WHEN '03' THEN 8
      WHEN '01' THEN 3 ELSE 0 END
  + CASE WHEN COALESCE(v.capital_social,0) >= 10000000 THEN 20
      WHEN COALESCE(v.capital_social,0) >= 1000000 THEN 15
      WHEN COALESCE(v.capital_social,0) >= 100000 THEN 10
      WHEN COALESCE(v.capital_social,0) >= 10000 THEN 5 ELSE 0 END
  + CASE WHEN NULLIF(btrim(v.cnae_fiscal_principal),'') IS NOT NULL THEN 5 ELSE 0 END
  + CASE WHEN v.opcao_simples='S' THEN 5 ELSE 0 END
)
"""


@dataclass(frozen=True)
class MarketingSettings:
    batch_size: int = int(os.getenv("MARKETING_BATCH_SIZE", "100000"))
    workers: int = int(os.getenv("MARKETING_EMAIL_WORKERS", "64"))
    min_score: int = int(os.getenv("PROSPECT_MIN_LEAD_SCORE", "70"))
    timeout_seconds: float = float(os.getenv("MARKETING_MX_TIMEOUT", "3"))


def _fetch_candidates(conn, settings: MarketingSettings) -> list[dict]:
    query = f"""
      SELECT v.cnpj,v.cnpj_basico,v.razao_social,v.nome_fantasia,v.uf,
        v.municipio_descricao,v.telefone_1,lower(btrim(v.email)) AS email,
        v.capital_social,v.opcao_mei,v.opcao_simples,v.source_competence,
        ({PRE_SCORE_SQL})::smallint AS pre_score
      FROM cnpj.v_prospect_candidates v
      LEFT JOIN etl.candidate_decisions decision ON decision.cnpj=v.cnpj
      LEFT JOIN intelligence.email_verifications ev ON ev.cnpj=v.cnpj
      WHERE decision.cnpj IS NULL
        AND (ev.cnpj IS NULL OR ev.expires_at <= now())
        AND ({PRE_SCORE_SQL}) >= %s
      ORDER BY ({PRE_SCORE_SQL}) DESC,v.cnpj
      LIMIT %s
    """
    params = (
        list(FREE_DOMAINS),
        list(FREE_DOMAINS),
        settings.min_score,
        list(FREE_DOMAINS),
        settings.batch_size,
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return list(cur.fetchall())


def _verify_groups(rows: list[dict], settings: MarketingSettings) -> dict[str, dict]:
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_domain[row["email"].rsplit("@", 1)[-1]].append(row)

    def verify_group(group: list[dict]) -> list[tuple[str, dict]]:
        return [
            (row["cnpj"], verify_email(row["email"], timeout=settings.timeout_seconds))
            for row in group
        ]

    verified: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, settings.workers)) as executor:
        futures = [executor.submit(verify_group, group) for group in by_domain.values()]
        for future in as_completed(futures):
            verified.update(future.result())
    return verified


def _stage(conn, rows: list[dict], verified: dict[str, dict]) -> None:
    conn.execute(
        """
        CREATE TEMP TABLE tmp_marketing_ready (
          cnpj char(14),cnpj_basico char(8),razao_social text,nome_fantasia text,
          uf char(2),municipio_descricao text,telefone_1 text,email text,
          capital_social numeric(18,2),opcao_mei char(1),opcao_simples char(1),
          source_competence text,pre_score smallint,domain text,syntax_valid boolean,
          mx_valid boolean,mx_hosts text[],disposable boolean,email_role text,
          email_type text,deliverability_status text,risk_score smallint,
          reason_codes text[],last_error text
        ) ON COMMIT DROP
        """
    )
    columns = (
        "cnpj","cnpj_basico","razao_social","nome_fantasia","uf",
        "municipio_descricao","telefone_1","email","capital_social","opcao_mei",
        "opcao_simples","source_competence","pre_score","domain","syntax_valid",
        "mx_valid","mx_hosts","disposable","email_role","email_type",
        "deliverability_status","risk_score","reason_codes","last_error",
    )
    with conn.cursor().copy(
        f"COPY tmp_marketing_ready ({','.join(columns)}) FROM STDIN"
    ) as copy:
        for row in rows:
            check = verified[row["cnpj"]]
            copy.write_row(
                [
                    row.get("cnpj"),row.get("cnpj_basico"),row.get("razao_social"),
                    row.get("nome_fantasia"),row.get("uf"),row.get("municipio_descricao"),
                    row.get("telefone_1"),row.get("email"),row.get("capital_social"),
                    row.get("opcao_mei"),row.get("opcao_simples"),
                    row.get("source_competence"),row.get("pre_score"),check.get("domain"),
                    check.get("syntax_valid"),check.get("mx_valid"),check.get("mx_hosts") or [],
                    check.get("disposable",False),check.get("email_role"),
                    check.get("email_type"),check.get("deliverability_status"),
                    check.get("risk_score",0),check.get("reason_codes") or [],check.get("error"),
                ]
            )


def _persist(conn) -> dict[str, int]:
    conn.execute(
        """
        INSERT INTO intelligence.email_verifications
          (cnpj,email,domain,syntax_valid,mx_valid,mx_hosts,disposable,email_role,
           deliverability_status,risk_score,reason_codes,checked_at,expires_at,last_error)
        SELECT cnpj,email,domain,syntax_valid,mx_valid,mx_hosts,disposable,email_role,
          deliverability_status,risk_score,reason_codes,now(),
          now()+CASE WHEN deliverability_status='unknown' THEN interval '1 hour'
                     ELSE interval '14 days' END,last_error
        FROM tmp_marketing_ready
        ON CONFLICT (cnpj) DO UPDATE SET email=EXCLUDED.email,domain=EXCLUDED.domain,
          syntax_valid=EXCLUDED.syntax_valid,mx_valid=EXCLUDED.mx_valid,
          mx_hosts=EXCLUDED.mx_hosts,disposable=EXCLUDED.disposable,
          email_role=EXCLUDED.email_role,deliverability_status=EXCLUDED.deliverability_status,
          risk_score=EXCLUDED.risk_score,reason_codes=EXCLUDED.reason_codes,
          checked_at=now(),expires_at=EXCLUDED.expires_at,last_error=EXCLUDED.last_error,
          updated_at=now()
        """
    )
    decisions = conn.execute(
        """
        INSERT INTO etl.candidate_decisions
          (cnpj,cnpj_basico,decision,profile_score,data_confidence_score,lead_score,
           razao_social,nome_fantasia,telefone,email,reason_codes,source_competence,
           evaluated_at,next_review_at,updated_at)
        SELECT cnpj,cnpj_basico,
          CASE WHEN deliverability_status IN ('valid','risky') THEN 'qualified_b'
               ELSE 'rejected' END,
          0,CASE WHEN deliverability_status='valid' THEN 8
                 WHEN deliverability_status='risky' THEN 7 ELSE 0 END,
          pre_score,
          CASE WHEN deliverability_status='invalid' THEN razao_social END,
          CASE WHEN deliverability_status='invalid' THEN nome_fantasia END,
          CASE WHEN deliverability_status='invalid' THEN telefone_1 END,
          CASE WHEN deliverability_status='invalid' THEN email END,
          reason_codes || ARRAY[
            CASE WHEN deliverability_status IN ('valid','risky')
              THEN 'marketing_email_ready' ELSE 'email_tecnicamente_invalido' END
          ],source_competence,now(),now()+interval '30 days',now()
        FROM tmp_marketing_ready
        WHERE deliverability_status IN ('valid','risky','invalid')
        ON CONFLICT (cnpj) DO UPDATE SET decision=EXCLUDED.decision,
          data_confidence_score=EXCLUDED.data_confidence_score,
          lead_score=EXCLUDED.lead_score,reason_codes=EXCLUDED.reason_codes,
          evaluated_at=now(),next_review_at=EXCLUDED.next_review_at,updated_at=now()
        """
    ).rowcount
    qualified = conn.execute(
        """
        INSERT INTO cnpj.prospectos_qualificados (
          cnpj,cnpj_basico,razao_social,nome_fantasia,uf,municipio_descricao,
          telefone_1,email,capital_social,opcao_mei,opcao_simples,digital_score,
          digital_maturity,fit_score,confidence_score,lead_score,lead_classification,
          qualification_status,lead_quality,qualification_reasons,contact_channel,
          contact_value,contact_confidence,contact_role,qualification_version,sinais,
          qualified_at,last_qualified_at,updated_at
        )
        SELECT cnpj,cnpj_basico,razao_social,nome_fantasia,uf,municipio_descricao,
          telefone_1,email,capital_social,opcao_mei,opcao_simples,pre_score,
          'email_validado',pre_score,
          CASE WHEN deliverability_status='valid' THEN 80 ELSE 70 END,
          pre_score,'marketing_ready','qualified','B',
          ARRAY['score_local_70_mais','email_mx_validado'],
          CASE WHEN email_type='gratuito' THEN 'email_gratuito' ELSE 'email_corporativo' END,
          email,CASE WHEN deliverability_status='valid' THEN 80 ELSE 70 END,email_role,
          'marketing-fast-v1',
          jsonb_build_object('fast_path',true,'email_status',deliverability_status,
            'email_risk_score',risk_score,'deep_enrichment_pending',true,
            'score_kind','preliminary','source_competence',source_competence),
          now(),now(),now()
        FROM tmp_marketing_ready
        WHERE deliverability_status IN ('valid','risky')
        ON CONFLICT (cnpj) DO UPDATE SET razao_social=EXCLUDED.razao_social,
          nome_fantasia=EXCLUDED.nome_fantasia,telefone_1=EXCLUDED.telefone_1,
          email=EXCLUDED.email,capital_social=EXCLUDED.capital_social,
          confidence_score=EXCLUDED.confidence_score,lead_score=EXCLUDED.lead_score,
          qualification_status='qualified',lead_quality='B',
          qualification_version=EXCLUDED.qualification_version,sinais=EXCLUDED.sinais,
          last_qualified_at=now(),updated_at=now()
        """
    ).rowcount
    return {"decisions": max(0, decisions), "qualified": max(0, qualified)}


def normalize_fast_scores(conn, *, limit: int = 10_000) -> dict:
    """Recalibra aos poucos o pre-score antigo que saturava em 100."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (7_262_603_883,))
    conn.execute("DROP TABLE IF EXISTS tmp_fast_reclassified")
    conn.execute(
        """
        CREATE TEMP TABLE tmp_fast_reclassified ON COMMIT DROP AS
        SELECT p.cnpj,
          LEAST(95,
            25
            + CASE WHEN lower(split_part(p.email,'@',2)) = ANY(%s) THEN 5 ELSE 15 END
            + CASE WHEN NULLIF(btrim(p.telefone_1),'') IS NOT NULL THEN 10 ELSE 0 END
            + CASE WHEN NULLIF(btrim(p.nome_fantasia),'') IS NOT NULL THEN 5 ELSE 0 END
            + CASE WHEN COALESCE(p.capital_social,0) >= 10000000 THEN 20
                WHEN COALESCE(p.capital_social,0) >= 1000000 THEN 15
                WHEN COALESCE(p.capital_social,0) >= 100000 THEN 10
                WHEN COALESCE(p.capital_social,0) >= 10000 THEN 5 ELSE 0 END
            + CASE WHEN p.opcao_simples='S' THEN 5 ELSE 0 END
          )::smallint AS new_score
        FROM cnpj.prospectos_qualificados p
        WHERE p.qualification_status='qualified'
          AND p.qualification_version='marketing-fast-v1'
          AND NOT COALESCE((p.sinais->>'score_recalibrated')::boolean,false)
        ORDER BY p.cnpj
        LIMIT %s
        """,
        (list(FREE_DOMAINS), max(1, limit)),
    )
    rows = conn.execute(
        """
        UPDATE cnpj.prospectos_qualificados p
        SET lead_score=t.new_score,digital_score=t.new_score,fit_score=t.new_score,
          sinais=COALESCE(p.sinais,'{}'::jsonb) || jsonb_build_object(
            'score_recalibrated',true,'score_kind','preliminary'),
          updated_at=now()
        FROM tmp_fast_reclassified t
        WHERE p.cnpj=t.cnpj
        RETURNING p.cnpj,t.new_score
        """
    ).fetchall()
    conn.execute(
        """
        UPDATE etl.candidate_decisions d
        SET decision=CASE WHEN t.new_score>=70 THEN 'qualified_b' ELSE 'rejected' END,
          lead_score=t.new_score,
          razao_social=CASE WHEN t.new_score<70 THEN p.razao_social END,
          nome_fantasia=CASE WHEN t.new_score<70 THEN p.nome_fantasia END,
          telefone=CASE WHEN t.new_score<70 THEN p.telefone_1 END,
          email=CASE WHEN t.new_score<70 THEN p.email END,
          reason_codes=CASE WHEN t.new_score<70
            THEN ARRAY['pre_score_recalibrado_abaixo_70'] ELSE d.reason_codes END,
          evaluated_at=now(),updated_at=now()
        FROM tmp_fast_reclassified t
        JOIN cnpj.prospectos_qualificados p ON p.cnpj=t.cnpj
        WHERE d.cnpj=t.cnpj
        """
    )
    rejected = conn.execute(
        """
        DELETE FROM outreach.leads lead
        USING tmp_fast_reclassified t
        WHERE lead.cnpj=t.cnpj AND t.new_score<70
        """
    ).rowcount
    conn.execute(
        """
        DELETE FROM cnpj.prospectos_qualificados p
        USING tmp_fast_reclassified t
        WHERE p.cnpj=t.cnpj AND t.new_score<70
        """
    )
    kept = [cnpj for cnpj, score in rows if score >= 70]
    return {
        "processed": len(rows),
        "kept": len(kept),
        "rejected": max(0, rejected),
        "cnpjs": kept,
    }


def upgrade_enriched_fast_leads(conn) -> dict:
    """Converte B provisório em A somente após confirmar sinal digital forte."""
    rows = conn.execute(
        """
        UPDATE cnpj.prospectos_qualificados p
        SET site_url=COALESCE(d.site_final_url,d.site_url),site_ativo=d.site_ativo,
          plataforma=d.plataforma,whatsapp_url=CASE WHEN d.whatsapp_valid THEN d.whatsapp_url END,
          instagram_url=d.instagram_url,linkedin_url=d.linkedin_url,
          digital_score=COALESCE(d.digital_score,d.lead_score,p.digital_score),
          digital_maturity=COALESCE(d.commerce_maturity,d.digital_maturity),
          presence_score=d.presence_score,commerce_score=d.commerce_score,
          fit_score=d.fit_score,pain_score=d.pain_score,
          confidence_score=GREATEST(p.confidence_score,COALESCE(d.confidence_score,0)),
          lead_score=COALESCE(d.lead_score,p.lead_score),
          lead_quality=CASE
            WHEN ev.deliverability_status='valid'
              AND COALESCE(d.lead_score,d.digital_score,0)>=70
              AND COALESCE(d.confidence_score,0)>=70
              AND (d.site_valid OR d.whatsapp_valid
                   OR d.google_business_status='OPERATIONAL')
            THEN 'A' ELSE 'B' END,
          qualification_reasons=CASE
            WHEN ev.deliverability_status='valid'
              AND COALESCE(d.lead_score,d.digital_score,0)>=70
              AND COALESCE(d.confidence_score,0)>=70
              AND (d.site_valid OR d.whatsapp_valid
                   OR d.google_business_status='OPERATIONAL')
            THEN ARRAY['email_validado','sinal_digital_forte','qualidade_a']
            ELSE ARRAY['email_validado','enriquecimento_digital','qualidade_b'] END,
          qualification_version='marketing-digital-v2',
          sinais=COALESCE(p.sinais,'{}'::jsonb) || COALESCE(d.sinais,'{}'::jsonb)
            || jsonb_build_object('deep_enrichment_pending',false,'score_kind','digital'),
          last_qualified_at=now(),updated_at=now()
        FROM cnpj.digital_presenca d
        LEFT JOIN intelligence.email_verifications ev ON ev.cnpj=d.cnpj
        WHERE p.cnpj=d.cnpj
          AND p.qualification_status='qualified'
          AND p.qualification_version LIKE 'marketing-fast-%'
          AND d.enrich_status IN ('done','partial','no_site','failed')
        RETURNING p.cnpj,p.lead_quality,p.lead_score
        """
    ).fetchall()
    if rows:
        conn.execute(
            """
            UPDATE etl.candidate_decisions decision
            SET decision=CASE WHEN p.lead_score<70 THEN 'rejected'
                WHEN p.lead_quality='A' THEN 'qualified_a' ELSE 'qualified_b' END,
              lead_score=p.lead_score,
              razao_social=CASE WHEN p.lead_score<70 THEN p.razao_social END,
              nome_fantasia=CASE WHEN p.lead_score<70 THEN p.nome_fantasia END,
              telefone=CASE WHEN p.lead_score<70 THEN p.telefone_1 END,
              email=CASE WHEN p.lead_score<70 THEN p.email END,
              reason_codes=CASE WHEN p.lead_score<70
                THEN ARRAY['score_digital_abaixo_70'] ELSE decision.reason_codes END,
              evaluated_at=now(),updated_at=now()
            FROM cnpj.prospectos_qualificados p
            WHERE decision.cnpj=p.cnpj AND p.cnpj=ANY(%s)
            """,
            ([cnpj for cnpj, _, _ in rows],),
        )
        low_score = [cnpj for cnpj, _, score in rows if score < 70]
        if low_score:
            conn.execute("DELETE FROM outreach.leads WHERE cnpj=ANY(%s)", (low_score,))
            conn.execute(
                "DELETE FROM cnpj.prospectos_qualificados WHERE cnpj=ANY(%s)",
                (low_score,),
            )
    kept = [cnpj for cnpj, _, score in rows if score >= 70]
    return {
        "processed": len(rows),
        "quality_a": sum(1 for _, quality, score in rows if quality == "A" and score >= 70),
        "quality_b": sum(1 for _, quality, score in rows if quality == "B" and score >= 70),
        "rejected": sum(1 for _, _, score in rows if score < 70),
        "cnpjs": kept,
    }


def publish_marketing_ready(
    conn, settings: MarketingSettings | None = None, *, commit: bool = True
) -> dict[str, int]:
    """Valida MX por domínio e publica B imediatamente, sem HTTP de site."""
    settings = settings or MarketingSettings()
    rows = _fetch_candidates(conn, settings)
    if not rows:
        return {"processed": 0, "qualified": 0, "invalid": 0, "unknown": 0, "outreach": 0}
    log.info(
        "[MARKETING-FAST] validando %s e-mails em %s workers (score >= %s)",
        len(rows),settings.workers,settings.min_score,
    )
    verified = _verify_groups(rows, settings)
    _stage(conn, rows, verified)
    persisted = _persist(conn)
    statuses = [item["deliverability_status"] for item in verified.values()]
    ready_cnpjs = [
        cnpj
        for cnpj, item in verified.items()
        if item["deliverability_status"] in {"valid", "risky"}
    ]
    outreach = sync_qualified_leads(conn, commit=False, cnpjs=ready_cnpjs)
    if commit:
        conn.commit()
    stats = {
        "processed": len(rows),
        "qualified": persisted["qualified"],
        "invalid": statuses.count("invalid"),
        "unknown": statuses.count("unknown"),
        "outreach": max(0, outreach),
    }
    log.info("[MARKETING-FAST] lote concluído: %s", stats)
    return stats
