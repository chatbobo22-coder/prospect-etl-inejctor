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
