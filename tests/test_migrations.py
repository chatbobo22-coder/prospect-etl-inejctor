from pathlib import Path


def test_migrations_idempotent():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    for path in sorted(sql_dir.glob("009_*.sql")) + sorted(sql_dir.glob("010_*.sql")):
        text = path.read_text(encoding="utf-8")
        assert "IF NOT EXISTS" in text or "CREATE OR REPLACE" in text or "DO $$" in text


def test_legacy_digital_view_is_recreated_safely():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    for migration in ("006_digital_presenca.sql", "007_digital_presenca_upgrade.sql"):
        text = (sql_dir / migration).read_text(encoding="utf-8")
        assert "DROP VIEW IF EXISTS cnpj.v_prospect_digital;" in text
        assert "CREATE VIEW cnpj.v_prospect_digital AS" in text
