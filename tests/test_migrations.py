from pathlib import Path


def test_migrations_idempotent():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    for path in (
        sorted(sql_dir.glob("009_*.sql"))
        + sorted(sql_dir.glob("010_*.sql"))
        + sorted(sql_dir.glob("011_*.sql"))
        + sorted(sql_dir.glob("012_*.sql"))
        + sorted(sql_dir.glob("013_*.sql"))
        + sorted(sql_dir.glob("014_*.sql"))
        + sorted(sql_dir.glob("016_*.sql"))
    ):
        text = path.read_text(encoding="utf-8")
        assert "IF NOT EXISTS" in text or "CREATE OR REPLACE" in text or "DO $$" in text


def test_legacy_digital_view_is_recreated_safely():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    for migration in (
        "006_digital_presenca.sql",
        "007_digital_presenca_upgrade.sql",
    ):
        text = (sql_dir / migration).read_text(encoding="utf-8")
        assert "DROP VIEW IF EXISTS cnpj.v_prospect_digital;" in text
        assert "CREATE VIEW cnpj.v_prospect_digital AS" in text


def test_company_intelligence_migration_has_no_volatile_partial_index():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    text = (sql_dir / "012_company_intelligence.sql").read_text(encoding="utf-8")
    assert "CREATE SCHEMA IF NOT EXISTS intelligence" in text
    assert "WHERE expires_at IS NULL OR expires_at > now()" not in text


def test_national_candidate_view_has_no_geographic_cutoff():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    text = (sql_dir / "013_national_quality_candidates.sql").read_text(encoding="utf-8")
    assert "v.uf" not in text
    assert "municipios_populacao" not in text
    assert "v.telefone_1" not in text
    assert "v.nome_fantasia" not in text
    assert "cnaes_fiscais_secundarios" in text


def test_qualification_view_can_be_replayed_after_v3():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    text = (sql_dir / "010_prospect_qualification_v2.sql").read_text(encoding="utf-8")
    assert "DROP VIEW IF EXISTS cnpj.v_prospectos_outreach_v3;" in text
    assert "DROP VIEW IF EXISTS cnpj.v_prospectos_outreach_v2;" in text


def test_intelligence_view_can_be_extended_and_replayed():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    text = (sql_dir / "012_company_intelligence.sql").read_text(encoding="utf-8")
    assert "DROP VIEW IF EXISTS intelligence.v_commercial_profiles;" in text


def test_commercial_quality_tables_are_indexed():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    text = (sql_dir / "014_commercial_quality_loop.sql").read_text(encoding="utf-8")
    for table in (
        "email_verifications",
        "commercial_feedback",
        "company_groups",
        "company_group_members",
        "company_technologies",
    ):
        assert f"intelligence.{table}" in text
    assert "idx_commercial_feedback_company_time" in text
    assert "idx_company_people_priority" in text


def test_quality_storage_funnel_keeps_only_ab_in_commercial_view():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    text = (sql_dir / "016_quality_storage_funnel.sql").read_text(encoding="utf-8")
    assert "etl.candidate_decisions" in text
    assert "decision IN ('qualified_a', 'qualified_b', 'rejected')" in text
    assert "p.lead_quality IN ('A', 'B')" in text
