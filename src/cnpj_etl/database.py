import logging
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)


class Database:
    def __init__(self, url: str):
        self.url = url

    def connect(self, *, autocommit: bool = False):
        return psycopg.connect(self.url, autocommit=autocommit, connect_timeout=30)

    def ping(self) -> str:
        with self.connect() as conn:
            return conn.execute("SELECT current_database(), version()").fetchone()[0]

    def needs_initial_load(self, conn) -> bool:
        return not conn.execute(
            "SELECT EXISTS (SELECT 1 FROM cnpj.empresas LIMIT 1) "
            "OR EXISTS (SELECT 1 FROM cnpj.estabelecimentos LIMIT 1)"
        ).fetchone()[0]

    def migrate(self, sql_dir: Path):
        with self.connect() as conn:
            for path in sorted(sql_dir.glob("*.sql")):
                conn.execute(path.read_text(encoding="utf-8"))
            conn.commit()

    def acquire_lock(self, conn) -> bool:
        return conn.execute("SELECT pg_try_advisory_lock(%s)", (7262603881,)).fetchone()[0]

    def record_run_failure(self, run_id: int, processed: int, total: int, error: Exception):
        message = str(error)[:4000]
        if "DiskFull" in type(error).__name__ or "No space left on device" in message:
            message = (
                f"{message} — disco do PostgreSQL/Supabase cheio. "
                "Use filtros CNAE ou faça upgrade do plano."
            )[:4000]
        try:
            with self.connect(autocommit=True) as conn:
                conn.execute(
                    "UPDATE etl.runs SET status='failed',finished_at=now(),"
                    "files_processed=%s,rows_processed=%s,error_message=%s WHERE id=%s",
                    (processed, total, message, run_id),
                )
        except Exception as log_exc:
            log.warning("Não foi possível registrar falha em etl.runs: %s", log_exc)
