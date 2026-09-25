"""Retenção mínima: remove triagem já decidida e mantém somente inteligência A/B."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def prune_evaluated_candidates(conn) -> dict[str, int]:
    """Materializa métricas e mantém detalhes somente dos leads aprovados."""
    stats: dict[str, int] = {}

    def execute(name: str, statement: str) -> None:
        result = conn.execute(statement)
        stats[name] = max(0, result.rowcount)

    # Consolida contadores antes de apagar as decisões linha a linha. Isso
    # preserva a telemetria sem carregar milhões de rejeições no banco.
    conn.execute(
        """
        INSERT INTO etl.funnel_metrics
          (singleton,rejected,rejected_below_score,rejected_pre_enrichment,updated_at)
        SELECT true,
          count(*) FILTER (WHERE decision='rejected'),
          count(*) FILTER (
            WHERE decision='rejected' AND EXISTS (
              SELECT 1 FROM unnest(reason_codes) reason
              WHERE reason LIKE 'lead_score_abaixo_%'
                 OR reason LIKE 'score_digital_abaixo_%'
            )
          ),
          count(*) FILTER (
            WHERE decision='rejected' AND EXISTS (
              SELECT 1 FROM unnest(reason_codes) reason WHERE reason LIKE 'pre_score%abaixo_%'
            )
          ),now()
        FROM etl.candidate_decisions
        ON CONFLICT (singleton) DO UPDATE SET
          rejected=etl.funnel_metrics.rejected+EXCLUDED.rejected,
          rejected_below_score=etl.funnel_metrics.rejected_below_score
            +EXCLUDED.rejected_below_score,
          rejected_pre_enrichment=etl.funnel_metrics.rejected_pre_enrichment
            +EXCLUDED.rejected_pre_enrichment,
          updated_at=now()
        """
    )

    # Nunca mantenha rejeitados na tabela consumida pelo outreach.
    execute(
        "prospects_rejected",
        """
        DELETE FROM cnpj.prospectos_qualificados p
        USING etl.candidate_decisions d
        WHERE p.cnpj=d.cnpj AND d.decision='rejected'
        """,
    )

    # Inteligência detalhada só é patrimônio comercial para leads aprovados.
    for name, table in (
        ("intent_alerts", "intelligence.intent_alert_events"),
        ("tironi_history", "intelligence.tironi_score_history"),
        ("tironi_profiles", "intelligence.tironi_profiles"),
        ("source_state", "intelligence.company_source_state"),
        ("people", "intelligence.company_people"),
        ("signals", "intelligence.company_signals"),
        ("profiles", "intelligence.company_profiles"),
        ("email_verifications", "intelligence.email_verifications"),
        ("technologies", "intelligence.company_technologies"),
        ("group_members", "intelligence.company_group_members"),
    ):
        execute(
            name,
            f"""
            DELETE FROM {table} item
            USING etl.candidate_decisions d
            WHERE item.cnpj=d.cnpj AND d.decision='rejected'
            """,
        )

    execute(
        "empty_groups",
        """
        DELETE FROM intelligence.company_groups g
        WHERE NOT EXISTS (
          SELECT 1 FROM intelligence.company_group_members m WHERE m.group_key=g.group_key
        )
        """,
    )
    execute(
        "digital_rejected",
        """
        DELETE FROM cnpj.digital_presenca item
        USING etl.candidate_decisions d
        WHERE item.cnpj=d.cnpj AND d.decision='rejected'
        """,
    )

    # O prospect A/B já está materializado. Rejeitados não conservam contato;
    # somente os contadores agregados acima permanecem.
    execute(
        "raw_establishments",
        """
        DELETE FROM cnpj.estabelecimentos item
        WHERE EXISTS (
          SELECT 1 FROM etl.candidate_decisions d WHERE d.cnpj=item.cnpj
        ) OR EXISTS (
          SELECT 1 FROM cnpj.prospectos_qualificados p WHERE p.cnpj=item.cnpj
        )
        """,
    )
    for name, table in (
        ("raw_partners", "cnpj.socios"),
        ("raw_simples", "cnpj.simples"),
        ("raw_companies", "cnpj.empresas"),
    ):
        execute(
            name,
            f"""
            DELETE FROM {table} item
            WHERE NOT EXISTS (
              SELECT 1 FROM cnpj.estabelecimentos e WHERE e.cnpj_basico=item.cnpj_basico
            )
            AND (
              EXISTS (
                SELECT 1 FROM etl.candidate_decisions d
                WHERE d.cnpj_basico=item.cnpj_basico
              ) OR EXISTS (
                SELECT 1 FROM cnpj.prospectos_qualificados p
                WHERE p.cnpj_basico=item.cnpj_basico
              )
            )
            """,
        )

    execute("decisions_compacted", "DELETE FROM etl.candidate_decisions")

    raw_remaining = conn.execute(
        "SELECT count(*) FROM cnpj.estabelecimentos"
    ).fetchone()[0]
    if not raw_remaining:
        # TRUNCATE devolve imediatamente as páginas das tabelas de staging;
        # DELETE/VACUUM comum apenas as deixaria reservadas para reúso.
        conn.execute(
            "TRUNCATE cnpj.socios,cnpj.simples,cnpj.empresas,cnpj.estabelecimentos"
        )
        stats["raw_tables_truncated"] = 1

    conn.commit()
    log.info("Retenção do funil aplicada: %s", stats)
    return stats
