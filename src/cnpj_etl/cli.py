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
from .outreach_sync import sync_qualified_leads
from .pipeline import run
from .prospect import CORE_INTELLIGENCE_SOURCES, promote_qualified, reject_before_intelligence
from .retention import prune_evaluated_candidates
from .source import RfbSource


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def run_fast_lead_cycle(conn, remote, rows: int) -> None:
    """Publica leads fortes durante a carga, sem esperar o último ZIP da Receita."""
    if remote.file_type != "Empresas" or rows <= 0:
        return

    batch_size = int(os.getenv("FAST_LEAD_BATCH_SIZE", "100"))
    lead_threshold = int(os.getenv("PROSPECT_MIN_LEAD_SCORE", "70"))
    logging.info(
        "[FAST-LEAD] %s carregado; validando até %s melhores candidatos agora",
        remote.name,
        batch_size,
    )
    enrich_stats = run_enrichment(conn, EnrichSettings(batch_size=batch_size))
    triage_stats = reject_before_intelligence(conn, min_lead_score=lead_threshold)
    retention_before = prune_evaluated_candidates(conn)

    intelligence_stats = run_intelligence(
        conn,
        replace(
            IntelligenceSettings(),
            sources=CORE_INTELLIGENCE_SOURCES,
            batch_size=batch_size,
            min_lead_score=lead_threshold,
            max_rounds=1,
        ),
    )
    qualify_stats = promote_qualified(conn)
    synced = sync_qualified_leads(conn)
    retention_after = prune_evaluated_candidates(conn)
    logging.info(
        "[FAST-LEAD] lote publicado: enrich=%s triagem=%s intelligence=%s "
        "qualify=%s outreach=%s retention_before=%s retention_after=%s",
        enrich_stats,
        triage_stats,
        intelligence_stats,
        qualify_stats,
        synced,
        retention_before,
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
            qualify_stats = promote_qualified(conn)
            synced = sync_qualified_leads(conn)
        logging.info(
            "Publicação incremental concluída: qualify=%s outreach=%s",
            qualify_stats,
            synced,
        )
    elif args.command == "prospect-pipeline":
        db.migrate(sql_dir)
        batch_size = args.batch_size or int(os.getenv("ENRICH_BATCH_SIZE", "500"))
        settings_obj = EnrichSettings(batch_size=batch_size)
        with db.connect() as conn:
            initial_triage = reject_before_intelligence(conn)
            initial_retention = prune_evaluated_candidates(conn)

            def triage_round(round_number: int, round_stats: dict) -> None:
                rejected = reject_before_intelligence(conn)
                retained = prune_evaluated_candidates(conn)
                logging.info(
                    "Funil contínuo rodada=%s enrich=%s rejeitados=%s retenção=%s",
                    round_number,
                    round_stats,
                    rejected,
                    retained,
                )

            enrich_stats = run_enrichment_until_empty(
                conn,
                settings_obj,
                force=args.force_enrich,
                after_round=triage_round,
            )
            final_triage = reject_before_intelligence(conn)
            pre_intelligence_retention = prune_evaluated_candidates(conn)

            def publish_round(round_number: int, round_stats: dict) -> None:
                qualify_round = promote_qualified(conn)
                synced_round = sync_qualified_leads(conn)
                logging.info(
                    "Publicação incremental rodada=%s intelligence=%s qualify=%s outreach=%s",
                    round_number,
                    round_stats,
                    qualify_round,
                    synced_round,
                )

            configured_intelligence = IntelligenceSettings()
            fast_sources = tuple(
                source for source in configured_intelligence.sources if source != "gdelt"
            )
            slow_sources = tuple(
                source for source in configured_intelligence.sources if source == "gdelt"
            )
            fast_stats = (
                run_intelligence_until_empty(
                    conn,
                    replace(configured_intelligence, sources=fast_sources),
                    after_round=publish_round,
                )
                if fast_sources
                else {"processed": 0, "rounds": 0}
            )
            # Publica assim que as fontes essenciais/rápidas terminam. GDELT é opcional,
            # limitado e executado depois para nunca segurar a fila comercial.
            qualify_fast = promote_qualified(conn)
            synced_fast = sync_qualified_leads(conn)
            logging.info(
                "Via rápida publicada: intelligence=%s qualify=%s outreach=%s",
                fast_stats,
                qualify_fast,
                synced_fast,
            )
            slow_stats = (
                run_intelligence(
                    conn,
                    replace(configured_intelligence, sources=slow_sources),
                )
                if slow_sources
                else {"processed": 0}
            )
            intelligence_stats = {"fast": fast_stats, "optional": slow_stats}
            qualify_stats = promote_qualified(conn)
            synced = sync_qualified_leads(conn)
            retention_stats = prune_evaluated_candidates(conn)
        logging.info(
            "Pipeline prospect: triagem_inicial=%s retenção_inicial=%s enrich=%s "
            "triagem_final=%s retenção_pre_inteligência=%s intelligence=%s "
            "qualify=%s outreach=%s retention=%s",
            initial_triage,
            initial_retention,
            enrich_stats,
            final_triage,
            pre_intelligence_retention,
            intelligence_stats,
            qualify_stats,
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
