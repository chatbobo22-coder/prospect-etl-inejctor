from cnpj_etl.filters import FilterContext, matches_estabelecimento, municipio_key
from cnpj_etl.ibge_population import rfb_municipio_code


def test_rfb_municipio_code():
    assert rfb_municipio_code(3550308) == "0308"
    assert rfb_municipio_code(4106902) == "6902"
    assert rfb_municipio_code(1100015) == "0015"


def test_municipio_key_padding():
    assert municipio_key({"uf": "sp", "municipio": "308"}) == ("SP", "0308")


def test_min_population_filter():
    allowed = frozenset({("SP", "0308"), ("PR", "6902")})
    ctx = FilterContext(
        frozenset(["4751201"]),
        active_only=True,
        min_population=100_000,
        allowed_municipios=allowed,
    )
    base = {
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
        "nome_fantasia": "Loja",
        "ddd1": "11",
        "telefone1": "999887766",
    }
    assert matches_estabelecimento({**base, "uf": "SP", "municipio": "0308"}, ctx)
    assert not matches_estabelecimento({**base, "uf": "SP", "municipio": "9999"}, ctx)
