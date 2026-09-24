"""Sincronização dos prospects A/B com a operação de outreach."""

import logging

log = logging.getLogger(__name__)


def sync_qualified_leads(conn, *, commit: bool = True) -> int:
    """Insere e atualiza somente leads A/B qualificados em ``outreach.leads``."""
    result = conn.execute(
        r"""
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
          to_jsonb(p) || jsonb_build_object(
            'tironi_score', t.tironi_score,
            'tironi_classification', t.classification,
            'why_this_lead', t.why_this_lead,
            'recommended_products', t.recommended_products,
            'recommended_plan', t.recommended_plan,
            'next_best_action', t.next_best_action
          ),
          'cnpj_etl',
          'ready',
          now()
        FROM cnpj.prospectos_qualificados p
        LEFT JOIN intelligence.tironi_profiles t ON t.cnpj=p.cnpj
        WHERE p.qualification_status = 'qualified'
          AND p.lead_quality IN ('A', 'B')
          AND p.email IS NOT NULL
          AND btrim(p.email) ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'
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
        """
    )
    if commit:
        conn.commit()
    log.info("Outreach sincronizado: %s leads A/B", result.rowcount)
    return result.rowcount
