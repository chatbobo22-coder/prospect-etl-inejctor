import cnpj_etl.intelligence.pipeline as pipeline
from cnpj_etl.intelligence.models import IntelligenceSettings, SourceResult


def test_until_empty_publishes_after_each_non_empty_round(monkeypatch):
    rounds = iter(
        [
            {"processed": 100, "success": 80, "no_data": 20, "failed": 0, "skipped": 0},
            {"processed": 50, "success": 40, "no_data": 8, "failed": 2, "skipped": 0},
            {"processed": 0, "success": 0, "no_data": 0, "failed": 0, "skipped": 0},
        ]
    )
    monkeypatch.setattr(pipeline, "run_intelligence", lambda *_args, **_kwargs: next(rounds))
    published = []

    stats = pipeline.run_intelligence_until_empty(
        object(),
        after_round=lambda number, result: published.append((number, result["processed"])),
    )

    assert published == [(1, 100), (2, 50)]
    assert stats["processed"] == 150
    assert stats["rounds"] == 3


def test_until_empty_stops_when_source_circuit_opens(monkeypatch):
    calls = []

    def run(*_args, **_kwargs):
        calls.append(1)
        return {
            "processed": 3,
            "success": 0,
            "no_data": 0,
            "failed": 0,
            "skipped": 3,
            "sources": {"gdelt": {"circuit_open": 1}},
        }

    monkeypatch.setattr(pipeline, "run_intelligence", run)

    stats = pipeline.run_intelligence_until_empty(object())

    assert len(calls) == 1
    assert stats["processed"] == 3
    assert stats["circuit_open"] == 1


class _Cursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.query = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchall(self):
        return self.rows


class _PendingConnection:
    def __init__(self):
        self.last_cursor = None

    def cursor(self, **_kwargs):
        self.last_cursor = _Cursor()
        return self.last_cursor


def test_gdelt_pending_queue_only_contains_published_quality_leads():
    conn = _PendingConnection()

    pipeline._pending_companies(conn, "gdelt", 25, force=False)

    assert "FROM cnpj.prospectos_qualificados prospect" in conn.last_cursor.query
    assert "prospect.lead_quality IN ('A','B')" in conn.last_cursor.query
    assert conn.last_cursor.params == ("gdelt", 70, 25)


def test_fast_source_pending_queue_does_not_require_published_lead():
    conn = _PendingConnection()

    pipeline._pending_companies(conn, "receita", 100, force=False)

    assert "FROM cnpj.prospectos_qualificados prospect" not in conn.last_cursor.query
    assert "COALESCE(d.lead_score,0) >= %s" in conn.last_cursor.query
    assert conn.last_cursor.params == ("receita", 70, 100)


class _RunConnection:
    def __init__(self):
        self.execute_calls = []

    def execute(self, query, params=None):
        self.execute_calls.append((query, params))
        return _Result((1,))

    def commit(self):
        pass

    def rollback(self):
        pass


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def test_gdelt_circuit_breaker_stops_batch_after_consecutive_transient_errors(monkeypatch):
    conn = _RunConnection()
    companies = [{"cnpj": str(index)} for index in range(10)]
    processed = []
    monkeypatch.setattr(pipeline, "_pending_companies", lambda *_args, **_kwargs: companies)
    monkeypatch.setattr(pipeline, "_mark_running", lambda *_args: None)
    monkeypatch.setattr(pipeline, "refresh_profile", lambda *_args: {})
    monkeypatch.setattr(pipeline.time, "sleep", lambda *_args: None)

    def collect(*_args):
        processed.append(1)
        return SourceResult(
            "gdelt",
            status="skipped",
            metadata={"reason": "rate_limited"},
        )

    monkeypatch.setattr(pipeline, "collect_source", collect)
    monkeypatch.setattr(pipeline, "_persist_result", lambda *_args: None)

    stats = pipeline._run_source(
        conn,
        "gdelt",
        IntelligenceSettings(gdelt_circuit_breaker_errors=3),
        force=False,
    )

    assert len(processed) == 3
    assert stats["processed"] == 3
    assert stats["circuit_open"] == 1
