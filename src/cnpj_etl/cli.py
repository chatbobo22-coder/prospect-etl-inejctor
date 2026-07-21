import argparse
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
from .pipeline import run
from .prospect import promote_qualified
from .source import RfbSource


def main():
    parser = argparse.ArgumentParser(description="ETL dos Dados Abertos do CNPJ")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Cria/atualiza o banco")
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
    prospect = sub.add_parser(
        "prospect-pipeline",
        help="Enriquece até esvaziar fila e qualifica prospects (pós-ETL)",
    )
    prospect.add_argument("--batch-size", type=int, help="CNPJs por rodada de enriquecimento")
    prospect.add_argument("--force-enrich", action="store_true", help="Reprocessa todos no enrich")
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
    sql_dir = Path(__file__).resolve().parents[2] / "sql"
    if args.command == "check-db":
        database = db.ping()
        logging.info("Conexão OK (database=%s)", database)
    elif args.command == "verify-filters":
        if not settings.filters_enabled():
            raise SystemExit(
                "Filtros CNAE desabilitados. Remova DISABLE_FILTERS ou defina FILTER_CNAES."
            )
        logging.info(
            "CNAEs (%s): %s", len(settings.filter_cnaes), ", ".join(sorted(settings.filter_cnaes))
        )
        logging.info("Ativas only: %s", settings.filter_active_only)
        logging.info("CNAE principal only: %s", not settings.filter_include_secondary_cnae)
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
    elif args.command == "prospect-pipeline":
        db.migrate(sql_dir)
        batch_size = args.batch_size or int(os.getenv("ENRICH_BATCH_SIZE", "500"))
        settings_obj = EnrichSettings(batch_size=batch_size)
        with db.connect() as conn:
            enrich_stats = run_enrichment_until_empty(conn, settings_obj, force=args.force_enrich)
            qualify_stats = promote_qualified(conn)
        logging.info("Pipeline prospect: enrich=%s qualify=%s", enrich_stats, qualify_stats)
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
    elif args.command == "migrate":
        db.migrate(sql_dir)
    else:
        db.migrate(sql_dir)
        run(
            settings,
            db,
            RfbSource(settings.base_url, settings.timeout),
            args.competence,
            args.force,
            args.auto,
        )


if __name__ == "__main__":
    main()
