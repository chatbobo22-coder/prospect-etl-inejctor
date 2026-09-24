import psycopg
import pytest

import cnpj_etl.cli as cli
from cnpj_etl.database import Database


def test_resolve_sql_dir_from_working_directory(tmp_path, monkeypatch):
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    (sql_dir / "001_schema.sql").write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SQL_DIR", raising=False)

    assert cli.resolve_sql_dir() == sql_dir.resolve()


def test_resolve_sql_dir_from_environment(tmp_path, monkeypatch):
    sql_dir = tmp_path / "migrations"
    sql_dir.mkdir()
    (sql_dir / "009_quality.sql").write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setenv("SQL_DIR", str(sql_dir))

    assert cli.resolve_sql_dir() == sql_dir.resolve()


def test_resolve_sql_dir_fails_clearly_when_package_has_no_sql(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SQL_DIR", raising=False)
    monkeypatch.setattr(cli, "__file__", str(tmp_path / "site-packages" / "cnpj_etl" / "cli.py"))

    with pytest.raises(RuntimeError, match="Diretório de migrations SQL não encontrado"):
        cli.resolve_sql_dir()


def test_migrate_file_executes_only_selected_sql(tmp_path):
    migration = tmp_path / "011_test.sql"
    migration.write_text("SELECT 1;", encoding="utf-8")

    class FakeConnection:
        def __init__(self):
            self.queries = []
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, query, params=None):
            self.queries.append((query, params))

        def commit(self):
            self.committed = True

    connection = FakeConnection()
    database = Database("postgresql://unused")
    database.connect = lambda: connection

    database.migrate_file(migration)

    assert connection.queries == [
        ("SELECT pg_advisory_lock(%s)", (7_262_603_882,)),
        ("SELECT 1;", None),
    ]
    assert connection.committed is True


def test_migrate_file_retries_a_deadlock(tmp_path, monkeypatch):
    migration = tmp_path / "018_retry.sql"
    migration.write_text("SELECT 1;", encoding="utf-8")

    class FakeConnection:
        def __init__(self):
            self.migration_attempts = 0
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, query, params=None):
            if query == "SELECT 1;":
                self.migration_attempts += 1
                if self.migration_attempts == 1:
                    raise psycopg.errors.DeadlockDetected("deadlock detected")

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    connection = FakeConnection()
    database = Database("postgresql://unused")
    database.connect = lambda: connection
    monkeypatch.setattr("cnpj_etl.database.time.sleep", lambda _: None)

    database.migrate_file(migration)

    assert connection.migration_attempts == 2
    assert connection.rollbacks == 1
    assert connection.commits == 1


def test_fast_lead_cycle_publishes_after_company_file(monkeypatch):
    calls = []

    monkeypatch.setattr(
        cli,
        "reject_before_enrichment",
        lambda *_a, **_k: calls.append("prefilter") or {},
    )
    monkeypatch.setattr(cli, "run_enrichment", lambda *_a, **_k: calls.append("enrich") or {})
    monkeypatch.setattr(
        cli,
        "reject_before_intelligence",
        lambda *_a, **_k: calls.append("triage") or {},
    )
    monkeypatch.setattr(
        cli,
        "prune_evaluated_candidates",
        lambda *_a, **_k: calls.append("prune") or {},
    )
    monkeypatch.setattr(
        cli,
        "run_intelligence",
        lambda *_a, **_k: calls.append("intelligence") or {},
    )
    monkeypatch.setattr(
        cli,
        "promote_qualified",
        lambda *_a, **_k: calls.append("qualify") or {},
    )
    monkeypatch.setattr(
        cli,
        "sync_qualified_leads",
        lambda *_a, **_k: calls.append("outreach") or 1,
    )
    remote = type("Remote", (), {"file_type": "Empresas", "name": "Empresas0.zip"})()

    cli.run_fast_lead_cycle(object(), remote, 100)

    assert calls == [
        "prefilter",
        "prune",
        "enrich",
        "triage",
        "prune",
        "intelligence",
        "qualify",
        "outreach",
        "prune",
    ]
