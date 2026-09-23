import pytest
from psycopg.errors import QueryCanceled

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
