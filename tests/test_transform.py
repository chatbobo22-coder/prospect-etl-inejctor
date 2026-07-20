from cnpj_etl.loader import date_value, transform


def test_date_value():
    assert str(date_value("20260131")) == "2026-01-31"
    assert date_value("") is None
    assert date_value("00000000") is None


def test_establishment_cnpj():
    columns = ["cnpj_basico", "cnpj_ordem", "cnpj_dv"]
    row = transform("Estabelecimentos", ["12345678", "0001", "90"], columns, "2026-07")
    assert row["cnpj"] == "12345678000190"
