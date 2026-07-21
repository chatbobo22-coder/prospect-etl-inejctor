from pathlib import Path


def test_migrations_idempotent():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    for path in sorted(sql_dir.glob("009_*.sql")) + sorted(sql_dir.glob("010_*.sql")):
        text = path.read_text(encoding="utf-8")
        assert "IF NOT EXISTS" in text or "CREATE OR REPLACE" in text or "DO $$" in text
