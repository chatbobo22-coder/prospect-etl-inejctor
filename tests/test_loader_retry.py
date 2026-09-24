import pytest
from psycopg.errors import QueryCanceled
from types import SimpleNamespace
from zipfile import ZipFile

from cnpj_etl import loader


class FakeConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_flush_chunk_splits_and_retries_after_statement_timeout(monkeypatch):
    conn = FakeConnection()
    attempts = []

    def fake_upsert(_conn, _table, rows, _conflict, **_kwargs):
        attempts.append(len(rows))
        if len(rows) == 1000:
            raise QueryCanceled("statement timeout")

    monkeypatch.setattr(loader, "upsert_chunk", fake_upsert)

    loader.flush_chunk(conn, "estabelecimentos", [{}] * 1000, "cnpj")

    assert attempts == [1000, 500, 500]
    assert conn.rollbacks == 1
    assert conn.commits == 2


def test_flush_chunk_stops_retrying_at_minimum_size(monkeypatch):
    conn = FakeConnection()

    def always_timeout(*_args, **_kwargs):
        raise QueryCanceled("statement timeout")

    monkeypatch.setattr(loader, "upsert_chunk", always_timeout)

    with pytest.raises(QueryCanceled):
        loader.flush_chunk(
            conn,
            "estabelecimentos",
            [{}] * loader.MIN_RETRY_CHUNK_SIZE,
            "cnpj",
        )

    assert conn.rollbacks == 1
    assert conn.commits == 0


def test_establishment_load_stops_at_limit_and_resumes_from_cursor(tmp_path, monkeypatch):
    archive = tmp_path / "Estabelecimentos0.zip"
    with ZipFile(archive, "w") as zipped:
        zipped.writestr("estabelecimentos.csv", "1\n2\n3\n4\n5\n")

    monkeypatch.setitem(loader.DATASETS, "Estabelecimentos", ("estabelecimentos", ["id"]))
    monkeypatch.setattr(
        loader,
        "transform",
        lambda _kind, row, _columns, _competence: {
            "cnpj": row[0].zfill(14),
            "cnpj_basico": row[0].zfill(8),
        },
    )
    monkeypatch.setattr(loader, "should_load_row", lambda *_args: True)
    monkeypatch.setattr(loader, "flush_chunk", lambda *_args, **_kwargs: None)

    def context():
        return SimpleNamespace(max_candidates=2, selected_cnpjs=set(), matched_basics=set())

    first = loader.load_zip(
        object(),
        archive,
        "Estabelecimentos",
        "2026-09",
        100,
        filter_ctx=context(),
        stop_at_candidate_limit=True,
    )
    second = loader.load_zip(
        object(),
        archive,
        "Estabelecimentos",
        "2026-09",
        100,
        filter_ctx=context(),
        start_row=first.scanned_rows,
        stop_at_candidate_limit=True,
    )

    assert int(first) == 2
    assert first.scanned_rows == 2
    assert first.completed is False
    assert int(second) == 2
    assert second.scanned_rows == 4
    assert second.completed is False
