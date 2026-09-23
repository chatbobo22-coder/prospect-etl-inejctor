from app import _sanitize_log_lines, _workflow_progress


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

