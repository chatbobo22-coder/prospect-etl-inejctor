from pathlib import Path

from app import _runtime_log_lines, _sanitize_log_lines, _workflow_progress


def test_workflow_progress_tracks_steps_without_reaching_100_early():
    steps = [
        {"status": "completed"},
        {"status": "in_progress"},
        {"status": "queued"},
        {"status": "queued"},
    ]
    assert _workflow_progress("in_progress", steps) == 31
    assert _workflow_progress("completed", steps) == 100


def test_runtime_logs_redact_database_and_secret_values():
    lines = _sanitize_log_lines(
        "DATABASE_URL=postgresql://user:pass@example.test/db\n"
        "API_KEY=super-secret\n"
        "Processando arquivo 1"
    )
    joined = "\n".join(lines)
    assert "super-secret" not in joined
    assert "user:pass" not in joined
    assert "Processando arquivo 1" in joined


def test_runtime_logs_finish_with_current_state_and_detailed_telemetry():
    run = {
        "status": "in_progress",
        "updated_at": "2026-09-23T15:00:00Z",
    }
    steps = [{"name": "Run ETL", "status": "in_progress"}]
    telemetry = {
        "etl_run": {
            "competence": "2026-08",
            "status": "running",
            "files_processed": 2,
            "files_total": 10,
            "rows_processed": 1234,
        },
        "files": [
            {
                "name": "Estabelecimentos0.zip",
                "type": "Estabelecimentos",
                "status": "processing",
                "rows": 321,
                "bytes": 2048,
            }
        ],
        "counts": {"companies": 100, "enriched": 40, "qualified": 12},
        "intelligence": {
            "completed_checks": 180,
            "profiles": 40,
            "failed_checks": 2,
            "current_source": "website",
        },
        "storage": {"database_bytes": 4096, "schemas": {"cnpj": 2048}},
    }

    lines = _runtime_log_lines(run, steps, telemetry, ["linha bruta"])

    assert any("[ARQUIVO:PROCESSING]" in line and "linhas=321" in line for line in lines)
    assert any("[ETL]" in line and "arquivos=2/10" in line for line in lines)
    assert any("qualificadas A/B=12" in line for line in lines)
    assert any(
        "[INTELIGÊNCIA]" in line and "consultas concluídas=180" in line and "fonte=website" in line
        for line in lines
    )
    assert lines[-1].startswith("[AGORA] Run ETL")


def test_runtime_telemetry_errors_are_redacted():
    lines = _runtime_log_lines(
        {"status": "completed", "updated_at": "2026-09-23T15:00:00Z"},
        [],
        {
            "etl_run": {
                "status": "failed",
                "error": "DATABASE_URL=postgresql://user:pass@example.test/db",
            },
            "counts": {},
            "storage": {},
        },
        [],
    )

    joined = "\n".join(lines)
    assert "user:pass" not in joined
    assert "DATABASE_URL=***" in joined


def test_runtime_telemetry_uses_exact_counts_not_postgres_estimates():
    source = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")

    assert "SELECT count(*) FROM cnpj.estabelecimentos" in source
    assert "SELECT count(*) FROM cnpj.digital_presenca" in source
    assert "SELECT count(*) FROM cnpj.prospectos_qualificados" in source
    assert "n_live_tup" not in source
    assert "reltuples" not in source
