import argparse
from dataclasses import replace
import logging
import os
from pathlib import Path

from .config import Settings
from .database import Database
from .digital_enricher import (
    EnrichSettings,
    requeue_enrichment,
    rescore_all,
    run_enrichment,
    run_enrichment_until_empty,
)
from .ibge_population import ensure_municipios_populacao
from .intelligence import IntelligenceSettings, run_intelligence, run_intelligence_until_empty
from .intent.service import rebuild_profiles
from .marketing import MarketingSettings, publish_marketing_ready
from .outreach_sync import sync_qualified_leads
from .pipeline import run
from .prospect import (
    promote_qualified,
    reject_before_enrichment,
)
from .retention import prune_evaluated_candidates
from .source import RfbSource
from .storage_cleanup import cleanup_storage


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def run_fast_lead_cycle(conn, remote, rows: int) -> None:
    """Publica leads fortes durante a carga, sem esperar o último ZIP da Receita."""
    if remote.file_type != "Empresas" or rows <= 0:
        return

    batch_size = int(os.getenv("MARKETING_BATCH_SIZE", "100000"))
    logging.info(
        "[FAST-LEAD] %s carregado; validando até %s e-mails sem crawling",
        remote.name,
        batch_size,
    )
    prefilter_stats = reject_before_enrichment(
        conn, min_pre_score=int(os.getenv("PROSPECT_MIN_LEAD_SCORE", "70"))
    )
    prefilter_retention = prune_evaluated_candidates(conn)
    publish_stats = publish_marketing_ready(
        conn, replace(MarketingSettings(), batch_size=batch_size)
    )
    retention_after = prune_evaluated_candidates(conn)
    logging.info(
        "[FAST-LEAD] lote publicado antes do enriquecimento profundo: "
        "prefilter=%s prefilter_retention=%s publish=%s retention=%s",
        prefilter_stats,
        prefilter_retention,
        publish_stats,
        retention_after,
    )


def resolve_sql_dir() -> Path:
    """Localiza migrations no checkout ou em caminho explicitamente configurado."""
    candidates = []
    configured = os.getenv("SQL_DIR", "").strip()
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path.cwd() / "sql",
            Path(__file__).resolve().parents[2] / "sql",
        ]
    )
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("*.sql")):
            return candidate.resolve()
    searched = ", ".join(str(path) for path in candidates)
    raise RuntimeError(
        "Diretório de migrations SQL não encontrado. "
        f"Defina SQL_DIR ou execute no checkout do projeto. Caminhos verificados: {searched}"
    )


def main():
    parser = argparse.ArgumentParser(description="ETL dos Dados Abertos do CNPJ")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Cria/atualiza o banco")
    migrate_file = sub.add_parser("migrate-file", help="Aplica uma migração SQL específica")
    migrate_file.add_argument("filename", help="Nome do arquivo dentro do diretório sql")
    sub.add_parser("check-db", help="Testa a conexão com o PostgreSQL")
    sub.add_parser("verify-filters", help="Valida filtros de carga antes do ETL")
    sub.add_parser("sync-ibge", help="Baixa população municipal do IBGE para o banco")
    reset = sub.add_parser("reset-load", help="Apaga dados CNPJ e histórico ETL para recarga total")
    reset.add_argument(
        "--yes",
        action="store_true",
        help="Confirma apagamento (obrigatório no CI)",
    )
    enrich = sub.add_parser("enrich-digital", help="Enriquece presença digital dos prospects")
    enrich.add_argument("--batch-size", type=int, help="Quantidade de CNPJs por execução")
    enrich.add_argument("--force", action="store_true", help="Reprocessa registros já enriquecidos")
    enrich.add_argument(
        "--until-empty",
        action="store_true",
        help="Repete enriquecimento até esvaziar a fila (ou ENRICH_MAX_ROUNDS)",
    )
    sub.add_parser(
        "qualify-prospects",
        help="Promove enriquecidos qualificados para cnpj.prospectos_qualificados",
    )
    sub.add_parser(
        "sync-outreach",
        help="Sincroniza leads A/B qualificados com outreach.leads",
    )
    sub.add_parser(
        "publish-ready",
        help="Qualifica candidatos prontos e publica A/B no Outreach",
    )
    sub.add_parser(
        "cleanup-storage",
        help="Remove intermediários decididos e compacta tabelas (exige carga pausada)",
    )
    prospect = sub.add_parser(
        "prospect-pipeline",
        help="Enriquece até esvaziar fila e qualifica prospects (pós-ETL)",
    )
    prospect.add_argument("--batch-size", type=int, help="CNPJs por rodada de enriquecimento")
    prospect.add_argument("--force-enrich", action="store_true", help="Reprocessa todos no enrich")
    intelligence = sub.add_parser(
        "intelligence-pipeline",
        help="Consulta fontes públicas por lote e atualiza perfis comerciais",
    )
    intelligence.add_argument("--batch-size", type=int, help="Empresas por fonte")
    intelligence.add_argument("--sources", help="Fontes separadas por vírgula")
    intelligence.add_argument("--force", action="store_true", help="Reconsulta mesmo dentro do TTL")
    intelligence.add_argument(
        "--until-empty", action="store_true", help="Processa até esvaziar a fila"
    )
    intent = sub.add_parser(
        "rebuild-intent", help="Recalcula Tironi Score, recomendações e histórico"
    )
    intent.add_argument("--limit", type=int, default=1000, help="Máximo de leads qualificados")
    rescore = sub.add_parser("rescore-digital", help="Recalcula scores v2 sem HTTP")
    rescore.add_argument("--version", default="v2", help="Versão alvo do score")
    requeue = sub.add_parser("requeue-enrichment", help="Recoloca registros antigos na fila")
    requeue.add_argument("--reason", default="version_upgrade", help="Motivo do requeue")
    execute = sub.add_parser("run", help="Executa uma sincronização")
    execute.add_argument("--competence", help="Competência YYYY-MM; padrão: mais recente")
    execute.add_argument("--force", action="store_true", help="Reprocessa arquivos concluídos")
    execute.add_argument(
        "--auto",
        action="store_true",
        help="Carga completa se a base estiver vazia; senão sincronização incremental",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
    settings, db = Settings(), Database(Settings().database_url)
    sql_dir = resolve_sql_dir()
    if args.command == "check-db":
        database = db.ping()
        logging.info("Conexão OK (database=%s)", database)
    elif args.command == "verify-filters":
        if not settings.filters_enabled():
            raise SystemExit(
                "Filtros CNAE desabilitados. Remova DISABLE_FILTERS ou defina FILTER_CNAES."
            )
        if settings.filter_cnaes:
            logging.info(
                "CNAEs (%s): %s",
                len(settings.filter_cnaes),
                ", ".join(sorted(settings.filter_cnaes)),
            )
            logging.info("CNAE principal only: %s", not settings.filter_include_secondary_cnae)
        else:
            logging.info("CNAEs: todos (sem lista fixa)")
        logging.info("Ativas only: %s", settings.filter_active_only)
        logging.info("Somente matrizes: %s", settings.filter_headquarters_only)
        logging.info("E-mail válido obrigatório: %s", settings.filter_require_email)
        logging.info("E-mail backoffice bloqueado: %s", settings.filter_block_backoffice_email)
        logging.info(
            "Limite de candidatos novos por execução: %s",
            settings.filter_max_candidates_per_run or "desligado",
        )
        logging.info("Nome fantasia obrigatório: %s", settings.filter_require_nome_fantasia)
        logging.info("Telefone válido obrigatório: %s", settings.filter_require_telefone)
        logging.info(
            "População mínima município: %s", settings.filter_min_population or "desligado"
        )
        if settings.filter_ufs:
            logging.warning(
                "FILTER_UF ativo (%s) — remova a variable FILTER_UF no GitHub para carga nacional",
                ",".join(sorted(settings.filter_ufs)),
            )
        else:
            logging.info("UFs: nacional (sem filtro de estado)")
    elif args.command == "sync-ibge":
        db.migrate(sql_dir)
        with db.connect() as conn:
            total = ensure_municipios_populacao(conn, year=settings.ibge_population_year)
            conn.commit()
        logging.info("IBGE sincronizado: %s municípios", total)
    elif args.command == "enrich-digital":
        db.migrate(sql_dir)
        batch_size = args.batch_size or int(os.getenv("ENRICH_BATCH_SIZE", "300"))
        settings_obj = EnrichSettings(batch_size=batch_size)
        with db.connect() as conn:
            if args.until_empty:
                stats = run_enrichment_until_empty(conn, settings_obj, force=args.force)
            else:
                stats = run_enrichment(conn, settings_obj, force=args.force)
        logging.info("Enriquecimento concluído: %s", stats)
    elif args.command == "qualify-prospects":
        db.migrate(sql_dir)
        with db.connect() as conn:
            stats = promote_qualified(conn)
        logging.info("Qualificação concluída: %s", stats)
    elif args.command == "sync-outreach":
        db.migrate(sql_dir)
        with db.connect() as conn:
            synced = sync_qualified_leads(conn)
        logging.info("Sincronização Outreach concluída: %s leads A/B", synced)
    elif args.command == "publish-ready":
        db.migrate(sql_dir)
        with db.connect() as conn:
            stats = publish_marketing_ready(conn)
        logging.info("Publicação rápida concluída: %s", stats)
    elif args.command == "cleanup-storage":
        db.migrate(sql_dir)
        stats = cleanup_storage(db)
        logging.info("Limpeza de armazenamento concluída: %s", stats)
    elif args.command == "prospect-pipeline":
        db.migrate(sql_dir)
        batch_size = args.batch_size or int(os.getenv("ENRICH_BATCH_SIZE", "250"))
        settings_obj = EnrichSettings(batch_size=batch_size)
        with db.connect() as conn:
            min_score = int(os.getenv("PROSPECT_MIN_LEAD_SCORE", "70"))
            prefilter_stats = reject_before_enrichment(conn, min_pre_score=min_score)
            prefilter_retention = prune_evaluated_candidates(conn)
            publish_stats = publish_marketing_ready(conn)
            # Crawling é aprofundamento, não porta de entrada. Uma rodada
            # pequena mantém o perfil evoluindo sem bloquear o próximo lote.
            enrich_stats = run_enrichment(
                conn, settings_obj, force=args.force_enrich
            )
            synced = sync_qualified_leads(conn)
            retention_stats = prune_evaluated_candidates(conn)
        logging.info(
            "Pipeline prospect rápido: prefilter=%s retention_before=%s "
            "publish=%s deep_enrich_bounded=%s outreach=%s retention_after=%s",
            prefilter_stats,
            prefilter_retention,
            publish_stats,
            enrich_stats,
            synced,
            retention_stats,
        )
    elif args.command == "intelligence-pipeline":
        db.migrate(sql_dir)
        sources = (
            tuple(item.strip().lower() for item in args.sources.split(",") if item.strip())
            if args.sources
            else IntelligenceSettings().sources
        )
        settings_obj = IntelligenceSettings(
            batch_size=args.batch_size or int(os.getenv("INTELLIGENCE_BATCH_SIZE", "100")),
            sources=sources,
        )
        with db.connect() as conn:
            if args.until_empty:
                stats = run_intelligence_until_empty(conn, settings_obj, force=args.force)
            else:
                stats = run_intelligence(conn, settings_obj, force=args.force)
        logging.info("Inteligência comercial concluída: %s", stats)
    elif args.command == "rebuild-intent":
        db.migrate(sql_dir)
        with db.connect() as conn:
            total = rebuild_profiles(conn, max(1, args.limit))
        logging.info("Perfis de intenção recalculados: %s", total)
    elif args.command == "rescore-digital":
        db.migrate(sql_dir)
        with db.connect() as conn:
            stats = rescore_all(conn, version=args.version)
        logging.info("Rescore concluído: %s", stats)
    elif args.command == "requeue-enrichment":
        db.migrate(sql_dir)
        with db.connect() as conn:
            count = requeue_enrichment(conn, reason=args.reason)
        logging.info("Requeue: %s registros", count)
    elif args.command == "reset-load":
        if not args.yes:
            raise SystemExit("Use --yes para confirmar apagamento dos dados CNPJ.")
        with db.connect() as conn:
            db.reset_load(conn)
            conn.commit()
        logging.info(
            "Base CNPJ limpa — próximo run fará carga completa (use --force se etl.files voltar)"
        )
    elif args.command == "migrate-file":
        filename = Path(args.filename).name
        if filename != args.filename or not filename.endswith(".sql"):
            raise SystemExit(
                "Informe somente o nome de um arquivo .sql do diretório de migrations."
            )
        migration = sql_dir / filename
        if not migration.is_file():
            raise SystemExit(f"Migration não encontrada: {filename}")
        db.migrate_file(migration)
        logging.info("Migration aplicada: %s", filename)
    elif args.command == "migrate":
        db.migrate(sql_dir)
    else:
        db.migrate(sql_dir)
        after_file = run_fast_lead_cycle if _env_flag("FAST_LEAD_MODE", True) else None
        run(
            settings,
            db,
            RfbSource(settings.base_url, settings.timeout, settings.mirror_url),
            args.competence,
            args.force,
            args.auto,
            after_file=after_file,
        )


if __name__ == "__main__":
    main()
