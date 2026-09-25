"""Sincronização dos prospects A/B com a operação de outreach."""

import logging

log = logging.getLogger(__name__)


def sync_qualified_leads(
    conn, *, commit: bool = True, cnpjs: list[str] | None = None
) -> int:
    """Insere e atualiza somente leads A/B qualificados em ``outreach.leads``."""
    candidate_filter = "AND p.cnpj = ANY(%s)" if cnpjs is not None else ""
    result = conn.execute(
        rf"""
        INSERT INTO outreach.leads
          (cnpj, company_name, trade_name, email, email_domain, phone, whatsapp, contact_role,
           lead_score, confidence_score, source_payload, source, status, updated_at)
        SELECT
          p.cnpj,
          p.razao_social,
          p.nome_fantasia,
          lower(btrim(p.email)),
          split_part(lower(btrim(p.email)), '@', 2),
          p.telefone_1,
          p.whatsapp_url,
          CASE
            WHEN split_part(lower(btrim(p.email)), '@', 1)
                   IN ('vendas', 'comercial', 'sales') THEN 'sales'
            WHEN split_part(lower(btrim(p.email)), '@', 1)
                   IN ('contato', 'atendimento', 'relacionamento', 'sac') THEN 'support'
            WHEN split_part(lower(btrim(p.email)), '@', 1)
                   IN ('financeiro', 'fiscal', 'nfe', 'contabilidade') THEN 'finance'
            ELSE 'general'
          END,
          COALESCE(t.tironi_score, p.lead_score),
          p.confidence_score,
          jsonb_strip_nulls(jsonb_build_object(
            'lead_quality', p.lead_quality,
            'qualification_reasons', p.qualification_reasons,
            'qualification_version', p.qualification_version,
            'contact_channel', p.contact_channel,
            'marketing_ready', true,
            'marketing_ready_at', COALESCE(p.qualified_at, now()),
            'tironi_score', t.tironi_score,
            'tironi_classification', t.classification,
            'why_this_lead', t.why_this_lead,
            'recommended_products', t.recommended_products,
            'recommended_plan', t.recommended_plan,
            'next_best_action', t.next_best_action
          )),
          'cnpj_etl',
          'ready',
          now()
        FROM cnpj.prospectos_qualificados p
        LEFT JOIN intelligence.tironi_profiles t ON t.cnpj=p.cnpj
        WHERE p.qualification_status = 'qualified'
          AND p.lead_quality IN ('A', 'B')
          AND p.email IS NOT NULL
          AND btrim(p.email) ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
          {candidate_filter}
        ON CONFLICT (cnpj) DO UPDATE SET
          company_name = EXCLUDED.company_name,
          trade_name = EXCLUDED.trade_name,
          email = EXCLUDED.email,
          email_domain = EXCLUDED.email_domain,
          phone = EXCLUDED.phone,
          whatsapp = EXCLUDED.whatsapp,
          contact_role = EXCLUDED.contact_role,
          lead_score = EXCLUDED.lead_score,
          confidence_score = EXCLUDED.confidence_score,
          source_payload = EXCLUDED.source_payload,
          source = EXCLUDED.source,
          updated_at = now()
        WHERE (
          outreach.leads.company_name,
          outreach.leads.trade_name,
          outreach.leads.email,
          outreach.leads.phone,
          outreach.leads.whatsapp,
          outreach.leads.contact_role,
          outreach.leads.lead_score,
          outreach.leads.confidence_score,
          outreach.leads.source_payload
        ) IS DISTINCT FROM (
          EXCLUDED.company_name,
          EXCLUDED.trade_name,
          EXCLUDED.email,
          EXCLUDED.phone,
          EXCLUDED.whatsapp,
          EXCLUDED.contact_role,
          EXCLUDED.lead_score,
          EXCLUDED.confidence_score,
          EXCLUDED.source_payload
        )
        """,
        (cnpjs,) if cnpjs is not None else None,
    )
    if conn.execute("SELECT to_regclass('outreach.lead_metrics') IS NOT NULL").fetchone()[0]:
        conn.execute(
            """
            INSERT INTO outreach.lead_metrics
              (singleton,total,quality_a,quality_b,with_whatsapp,public_profile,
               score_sum,updated_at)
            SELECT true,
              count(*) FILTER (WHERE email IS NOT NULL),
              count(*) FILTER (
                WHERE email IS NOT NULL AND source_payload->>'lead_quality'='A'
              ),
              count(*) FILTER (
                WHERE email IS NOT NULL AND source_payload->>'lead_quality'='B'
              ),
              count(*) FILTER (
                WHERE email IS NOT NULL AND NULLIF(whatsapp,'') IS NOT NULL
              ),
              count(*) FILTER (
                WHERE email IS NOT NULL
                  AND source_payload->'qualification_reasons'
                    ? 'perfil_publico_verificado'
              ),
              COALESCE(sum(COALESCE(lead_score,0)) FILTER (WHERE email IS NOT NULL),0),
              now()
            FROM outreach.leads
            ON CONFLICT (singleton) DO UPDATE SET
              total=EXCLUDED.total,quality_a=EXCLUDED.quality_a,
              quality_b=EXCLUDED.quality_b,with_whatsapp=EXCLUDED.with_whatsapp,
              public_profile=EXCLUDED.public_profile,score_sum=EXCLUDED.score_sum,
              updated_at=now()
            """
        )
    if commit:
        conn.commit()
    log.info("Outreach sincronizado: %s leads A/B", result.rowcount)
    return result.rowcount
