import argparse
import logging
from pathlib import Path

from .config import Settings
from .database import Database
from .pipeline import run
from .source import RfbSource


def main():
    parser = argparse.ArgumentParser(description="ETL dos Dados Abertos do CNPJ")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Cria/atualiza o banco")
    sub.add_parser("check-db", help="Testa a conexão com o PostgreSQL")
    execute = sub.add_parser("run", help="Executa uma sincronização")
    execute.add_argument("--competence", help="Competência YYYY-MM; padrão: mais recente")
    execute.add_argument("--force", action="store_true", help="Reprocessa arquivos concluídos")
    execute.add_argument(
        "--auto",
        action="store_true",
        help="Carga completa se a base estiver vazia; senão sincronização incremental",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings, db = Settings(), Database(Settings().database_url)
    sql_dir = Path(__file__).resolve().parents[2] / "sql"
    if args.command == "check-db":
        database = db.ping()
        logging.info("Conexão OK (database=%s)", database)
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
