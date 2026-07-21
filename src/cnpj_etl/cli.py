import argparse
import logging
import os
from pathlib import Path

from .config import Settings
from .database import Database
from .digital_enricher import EnrichSettings, run_enrichment
from .ibge_population import ensure_municipios_populacao
from .pipeline import run
from .source import RfbSource


def main():
    parser = argparse.ArgumentParser(description="ETL dos Dados Abertos do CNPJ")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Cria/atualiza o banco")
    sub.add_parser("check-db", help="Testa a conexão com o PostgreSQL")
    sub.add_parser("verify-filters", help="Valida filtros de carga antes do ETL")
    sub.add_parser("sync-ibge", help="Baixa população municipal do IBGE para o banco")
    enrich = sub.add_parser("enrich-digital", help="Enriquece presença digital dos prospects")
    enrich.add_argument("--batch-size", type=int, help="Quantidade de CNPJs por execução")
    enrich.add_argument("--force", action="store_true", help="Reprocessa registros já enriquecidos")
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
        logging.info("CNAEs (%s): %s", len(settings.filter_cnaes), ", ".join(sorted(settings.filter_cnaes)))
        logging.info("Ativas only: %s", settings.filter_active_only)
        logging.info("CNAE principal only: %s", not settings.filter_include_secondary_cnae)
        logging.info("Nome fantasia obrigatório: %s", settings.filter_require_nome_fantasia)
        logging.info("Telefone válido obrigatório: %s", settings.filter_require_telefone)
        logging.info("População mínima município: %s", settings.filter_min_population or "desligado")
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
            stats = run_enrichment(conn, settings_obj, force=args.force)
        logging.info("Enriquecimento concluído: %s", stats)
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
