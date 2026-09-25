from cnpj_etl import marketing


def test_email_checks_are_grouped_by_domain_and_keep_each_mailbox(monkeypatch):
    calls = []

    def fake_verify(email, timeout):
        calls.append((email, timeout))
        return {"deliverability_status": "valid", "email": email}

    monkeypatch.setattr(marketing, "verify_email", fake_verify)
    rows = [
        {"cnpj": "1", "email": "dono@empresa.com.br"},
        {"cnpj": "2", "email": "vendas@empresa.com.br"},
        {"cnpj": "3", "email": "pessoa@gmail.com"},
    ]
    settings = marketing.MarketingSettings(
        batch_size=100, workers=2, min_score=70, timeout_seconds=2
    )

    checked = marketing._verify_groups(rows, settings)

    assert set(checked) == {"1", "2", "3"}
    assert len(calls) == 3
    assert all(timeout == 2 for _, timeout in calls)


def test_default_hot_path_excludes_partner_files():
    from cnpj_etl.filters import FILTER_FILE_TYPES

    assert "Socios" not in FILTER_FILE_TYPES


class _Result:
    def __init__(self, *, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows


class _MarketingConnection:
    def __init__(self, returned_rows):
        self.returned_rows = returned_rows
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if "RETURNING p.cnpj,t.new_score" in query:
            return _Result(rows=self.returned_rows)
        if "RETURNING p.cnpj,p.lead_quality,p.lead_score" in query:
            return _Result(rows=self.returned_rows)
        if "DELETE FROM outreach.leads" in query:
            return _Result(rowcount=1)
        return _Result()


def test_fast_score_recalibration_rejects_below_70_and_keeps_rest():
    conn = _MarketingConnection([("1", 82), ("2", 64)])

    stats = marketing.normalize_fast_scores(conn, limit=2)

    assert stats == {"processed": 2, "kept": 1, "rejected": 1, "cnpjs": ["1"]}
    assert any("LEAST(95" in query for query, _ in conn.queries)
    assert any("pre_score_recalibrado_abaixo_70" in query for query, _ in conn.queries)


def test_enriched_fast_leads_become_a_b_or_are_rejected():
    conn = _MarketingConnection([("1", "A", 91), ("2", "B", 73), ("3", "B", 58)])

    stats = marketing.upgrade_enriched_fast_leads(conn)

    assert stats == {
        "processed": 3,
        "quality_a": 1,
        "quality_b": 1,
        "rejected": 1,
        "cnpjs": ["1", "2"],
    }
    assert any("sinal_digital_forte" in query for query, _ in conn.queries)
    assert any("score_digital_abaixo_70" in query for query, _ in conn.queries)
