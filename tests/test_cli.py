import pytest

import cnpj_etl.cli as cli


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
