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
    }
    assert matches_estabelecimento(item, ctx)


def test_inactive_rejected():
    ctx = FilterContext(frozenset(["4751201"]), active_only=True)
    item = {"situacao_cadastral": "08", "cnae_fiscal_principal": "4751201"}
    assert not matches_estabelecimento(item, ctx)


def test_secondary_cnae_match():
    ctx = FilterContext(frozenset(["4781400"]), active_only=True)
    item = {
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "1234567",
        "cnaes_fiscais_secundarios": "1111111,4781400",
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
    }
    assert not matches_estabelecimento(item, ctx)
    item["uf"] = "PR"
    assert matches_estabelecimento(item, ctx)


def test_default_cnaes_count():
    assert len(DEFAULT_FILTER_CNAES) == 18
