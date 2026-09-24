import logging
import time
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)

MIGRATION_LOCK_ID = 7_262_603_882
MIGRATION_MAX_ATTEMPTS = 5
MIGRATION_RETRY_BASE_SECONDS = 0.5
RETRYABLE_TRANSACTION_ERRORS = (
    psycopg.errors.DeadlockDetected,
    psycopg.errors.SerializationFailure,
)


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
            conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
            for path in sorted(sql_dir.glob("*.sql")):
                self._execute_migration(conn, path)

    def migrate_file(self, path: Path):
        with self.connect() as conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_ID,))
            self._execute_migration(conn, path)

    def _execute_migration(self, conn, path: Path):
        """Executa um arquivo em transação curta e repete conflitos transitórios."""
        sql = path.read_text(encoding="utf-8")
        for attempt in range(1, MIGRATION_MAX_ATTEMPTS + 1):
            try:
                conn.execute(sql)
                conn.commit()
                return
            except RETRYABLE_TRANSACTION_ERRORS as exc:
                conn.rollback()
                if attempt == MIGRATION_MAX_ATTEMPTS:
                    raise
                delay = MIGRATION_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                log.warning(
                    "Conflito transitório na migration %s (%s). "
                    "Nova tentativa %s/%s em %.1fs.",
                    path.name,
                    exc.sqlstate or type(exc).__name__,
                    attempt + 1,
                    MIGRATION_MAX_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)

    def reset_load(self, conn):
        conn.execute(
            "TRUNCATE cnpj.socios, cnpj.simples, cnpj.estabelecimentos, cnpj.empresas, "
            "cnpj.cnaes CASCADE"
        )
        has_digital = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'cnpj' AND table_name = 'digital_presenca')"
        ).fetchone()[0]
        if has_digital:
            conn.execute("TRUNCATE cnpj.digital_presenca")
        conn.execute("DELETE FROM etl.files")
        conn.execute("DELETE FROM etl.runs")

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
