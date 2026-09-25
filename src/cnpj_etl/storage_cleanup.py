"""Retenção e compactação segura da base operacional."""

from __future__ import annotations

import logging

from .retention import prune_evaluated_candidates

log = logging.getLogger(__name__)

COMPACT_TABLES = (
    "cnpj.estabelecimentos",
    "cnpj.empresas",
    "cnpj.simples",
    "cnpj.socios",
    "cnpj.digital_presenca",
    "intelligence.company_source_state",
    "intelligence.company_signals",
    "intelligence.company_people",
    "intelligence.company_profiles",
    "intelligence.email_verifications",
    "intelligence.company_technologies",
    "intelligence.company_group_members",
    "intelligence.company_groups",
    "intelligence.intent_alert_events",
    "intelligence.tironi_score_history",
    "intelligence.tironi_profiles",
    "intelligence.source_runs",
    "etl.candidate_decisions",
    "etl.processed_candidates",
    "etl.enrichment_runs",
    "etl.files",
    "etl.runs",
)


def cleanup_storage(db) -> dict:
    """Remove intermediários decididos e devolve espaço físico ao Postgres."""
    with db.connect() as conn:
        running = conn.execute(
            "SELECT count(*) FROM etl.runs WHERE status='running'"
        ).fetchone()[0]
        if running:
            raise RuntimeError(
                "Existe uma carga ativa. Pause o Injector antes de liberar armazenamento."
            )
        before = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        pruned = prune_evaluated_candidates(conn)
        old_enrichment = conn.execute(
            """
            DELETE FROM etl.enrichment_runs
            WHERE status <> 'running'
              AND id NOT IN (SELECT id FROM etl.enrichment_runs ORDER BY id DESC LIMIT 20)
            """
        ).rowcount
        old_source_runs = conn.execute(
            """
            DELETE FROM intelligence.source_runs
            WHERE status <> 'running'
              AND id NOT IN (SELECT id FROM intelligence.source_runs ORDER BY id DESC LIMIT 50)
            """
        ).rowcount
        old_etl_runs = conn.execute(
            """
            DELETE FROM etl.runs
            WHERE status <> 'running'
              AND id NOT IN (SELECT id FROM etl.runs ORDER BY id DESC LIMIT 20)
            """
        ).rowcount
        expired_registry = conn.execute(
            "DELETE FROM etl.processed_candidates WHERE next_review_at <= now()"
        ).rowcount
        conn.commit()

    compacted = []
    with db.connect(autocommit=True) as conn:
        for table in COMPACT_TABLES:
            if not conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]:
                continue
            log.info("[ARMAZENAMENTO] compactando %s", table)
            conn.execute(f"VACUUM (FULL, ANALYZE) {table}")
            compacted.append(table)
        after = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    stats = {
        "before_bytes": before,
        "after_bytes": after,
        "released_bytes": max(0, before - after),
        "pruned": pruned,
        "old_enrichment_runs": max(0, old_enrichment),
        "old_source_runs": max(0, old_source_runs),
        "old_etl_runs": max(0, old_etl_runs),
        "expired_registry": max(0, expired_registry),
        "compacted_tables": compacted,
    }
    log.info("[ARMAZENAMENTO] limpeza concluída: %s", stats)
    return stats
