from pathlib import Path
import psycopg


class Database:
    def __init__(self, url: str):
        self.url = url

    def connect(self):
        return psycopg.connect(self.url, autocommit=False, connect_timeout=30)

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
