"""Retenção mínima: remove triagem já decidida e mantém somente inteligência A/B."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def prune_evaluated_candidates(conn) -> dict[str, int]:
    """Remove dados intermediários depois que um candidato recebeu decisão definitiva."""
    stats: dict[str, int] = {}

    def execute(name: str, statement: str) -> None:
        result = conn.execute(statement)
        stats[name] = max(0, result.rowcount)

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

    # Rejeitados saem imediatamente. Aprovados pelo caminho rápido mantêm o
    # cadastro bruto até o enriquecimento digital assíncrono terminar.
    execute(
        "raw_establishments",
        """
        DELETE FROM cnpj.estabelecimentos item
        USING etl.candidate_decisions d
        WHERE item.cnpj=d.cnpj
          AND (
            d.decision='rejected'
            OR EXISTS (
              SELECT 1 FROM cnpj.digital_presenca digital
              WHERE digital.cnpj=item.cnpj
                AND digital.enrich_status IN ('done','partial','no_site','failed')
            )
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
            AND EXISTS (
              SELECT 1 FROM etl.candidate_decisions d WHERE d.cnpj_basico=item.cnpj_basico
            )
            """,
        )

    conn.commit()
    log.info("Retenção do funil aplicada: %s", stats)
    return stats
