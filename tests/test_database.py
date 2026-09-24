from cnpj_etl.database import Database


def test_reset_load_removes_decisions_that_would_block_next_batch():
    class Result:
        def fetchone(self):
            return (True,)

    class Connection:
        def __init__(self):
            self.calls = []

        def execute(self, query, params=None):
            self.calls.append((query, params))
            return Result()

    conn = Connection()
    Database("postgresql://unused").reset_load(conn)
    statements = "\n".join(query for query, _ in conn.calls)

    assert "etl.candidate_decisions" in statements
    assert "intelligence.tironi_profiles" in statements
    assert "RESTART IDENTITY CASCADE" in statements
    assert statements.index("etl.candidate_decisions") < statements.index("DELETE FROM etl.files")
