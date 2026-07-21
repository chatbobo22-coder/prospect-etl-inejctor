from cnpj_etl.filters import (
    DEFAULT_FILTER_CNAES,
    FilterContext,
    matches_estabelecimento,
    should_load_row,
    track_estabelecimento,
)


def test_active_cnae_match():
    ctx = FilterContext(frozenset(["4751201"]), active_only=True)
    item = {
        "cnpj_basico": "12345678",
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
        "cnaes_fiscais_secundarios": "",
        "nome_fantasia": "Papelaria",
        "ddd1": "41",
        "telefone1": "33334444",
    }
    assert matches_estabelecimento(item, ctx)


def test_inactive_rejected():
    ctx = FilterContext(frozenset(["4751201"]), active_only=True)
    item = {"situacao_cadastral": "08", "cnae_fiscal_principal": "4751201"}
    assert not matches_estabelecimento(item, ctx)


def test_secondary_cnae_rejected_by_default():
    ctx = FilterContext(frozenset(["4781400"]), active_only=True)
    item = {
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "1234567",
        "cnaes_fiscais_secundarios": "1111111,4781400",
    }
    assert not matches_estabelecimento(item, ctx)


def test_secondary_cnae_match_when_enabled():
    ctx = FilterContext(
        frozenset(["4781400"]),
        active_only=True,
        include_secondary_cnae=True,
    )
    item = {
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "1234567",
        "cnaes_fiscais_secundarios": "1111111,4781400",
        "nome_fantasia": "Moda",
        "ddd1": "11",
        "telefone1": "988776655",
    }
    assert matches_estabelecimento(item, ctx)


def test_empresa_follows_estabelecimento():
    ctx = FilterContext(frozenset(["4751201"]))
    track_estabelecimento({"cnpj_basico": "12345678"}, ctx)
    assert should_load_row("Empresas", {"cnpj_basico": "12345678"}, ctx)
    assert not should_load_row("Empresas", {"cnpj_basico": "99999999"}, ctx)


def test_uf_filter():
    ctx = FilterContext(frozenset(["4751201"]), active_only=True, ufs=frozenset(["PR"]))
    item = {
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
        "uf": "SP",
        "nome_fantasia": "Loja",
        "ddd1": "41",
        "telefone1": "999887766",
    }
    assert not matches_estabelecimento(item, ctx)
    item["uf"] = "PR"
    assert matches_estabelecimento(item, ctx)


def test_default_cnaes_count():
    assert len(DEFAULT_FILTER_CNAES) == 18


def test_default_cnaes_match_user_list():
    expected = {
        "4791201",
        "4781400",
        "4782201",
        "4782202",
        "4783101",
        "4783102",
        "4772500",
        "4763601",
        "4763602",
        "4755503",
        "4754701",
        "4753900",
        "4751201",
        "4752100",
        "4789001",
        "4759899",
        "4530703",
        "4744099",
    }
    assert DEFAULT_FILTER_CNAES == frozenset(expected)


def test_rejects_empty_nome_fantasia():
    ctx = FilterContext(frozenset(["4751201"]), active_only=True)
    item = {
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
        "nome_fantasia": "",
        "ddd1": "41",
        "telefone1": "999887766",
    }
    assert not matches_estabelecimento(item, ctx)


def test_rejects_missing_telefone():
    ctx = FilterContext(frozenset(["4751201"]), active_only=True)
    item = {
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
        "nome_fantasia": "Loja Teste",
        "ddd1": "",
        "telefone1": "",
    }
    assert not matches_estabelecimento(item, ctx)


def test_accepts_valid_prospect_row():
    ctx = FilterContext(frozenset(["4751201"]), active_only=True)
    item = {
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
        "nome_fantasia": "Loja Teste",
        "ddd1": "41",
        "telefone1": "999887766",
    }
    assert matches_estabelecimento(item, ctx)


def test_rejects_all_zero_phone():
    ctx = FilterContext(frozenset(["4751201"]), active_only=True)
    item = {
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
        "nome_fantasia": "Loja Teste",
        "ddd1": "41",
        "telefone1": "000000000",
    }
    assert not matches_estabelecimento(item, ctx)
