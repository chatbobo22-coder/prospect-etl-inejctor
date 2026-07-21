from unittest.mock import MagicMock

import pytest

from cnpj_etl.digital_enricher import (
    fetch_pending,
    resolve_candidate_view,
    run_enrichment_until_empty,
)
from cnpj_etl.enrichment.models import EnrichSettings


def test_resolve_candidate_view_whitelist():
    assert resolve_candidate_view("cnpj.v_prospect_candidates") == "cnpj.v_prospect_candidates"
    with pytest.raises(ValueError):
        resolve_candidate_view("cnpj.evil_view")


def test_fetch_pending_rejects_invalid_view():
    conn = MagicMock()
    with pytest.raises(ValueError):
        fetch_pending(conn, 10, candidate_view="public.users")


def test_until_empty_tracks_processed(monkeypatch):
    calls = {"n": 0}
    batch = [
        {"cnpj": "00000000000101", "cnpj_basico": "00000001", "enrich_attempts": 0},
        {"cnpj": "00000000000102", "cnpj_basico": "00000001", "enrich_attempts": 0},
    ]

    def fake_fetch(conn, limit, **kwargs):
        calls["n"] += 1
        exclude = kwargs.get("exclude_cnpjs") or set()
        remaining = [r for r in batch if r["cnpj"] not in exclude]
        return remaining[:1]

    def fake_enrich(row, conn, settings):
        from cnpj_etl.enrichment.models import EnrichResult

        return EnrichResult(cnpj=row["cnpj"], cnpj_basico=row["cnpj_basico"], enrich_status="done")

    monkeypatch.setattr("cnpj_etl.digital_enricher.fetch_pending", fake_fetch)
    monkeypatch.setattr("cnpj_etl.digital_enricher.enrich_record", fake_enrich)
    monkeypatch.setattr("cnpj_etl.digital_enricher.upsert_result", lambda *a, **k: None)
    monkeypatch.setattr("cnpj_etl.digital_enricher.acquire_enrichment_lock", lambda c: True)
    monkeypatch.setattr("cnpj_etl.digital_enricher.release_enrichment_lock", lambda c: None)
    monkeypatch.setattr("cnpj_etl.digital_enricher.ensure_digital_presenca_schema", lambda c: None)

    conn = MagicMock()

    def _execute(sql, params=None):
        mock = MagicMock()
        if "INSERT INTO etl.enrichment_runs" in sql:
            mock.fetchone.return_value = (1,)
        else:
            mock.fetchone.return_value = None
        mock.rowcount = 1
        return mock

    conn.execute.side_effect = _execute

    settings = EnrichSettings(max_rounds=5, batch_size=1)
    stats = run_enrichment_until_empty(conn, settings, force=True)
    assert stats["processed"] == 2
    assert calls["n"] >= 2
