from datetime import date

from cnpj_etl.filters import (
    FILE_LOAD_ORDER,
    FilterContext,
    has_eligible_email,
    has_minimum_activity_age,
    matches_estabelecimento,
    should_load_row,
    track_estabelecimento,
)


def test_simples_is_loaded_before_company_for_early_mei_exclusion():
    assert FILE_LOAD_ORDER["Estabelecimentos"] < FILE_LOAD_ORDER["Simples"]
    assert FILE_LOAD_ORDER["Simples"] < FILE_LOAD_ORDER["Empresas"]


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


def test_empty_cnae_list_accepts_any_activity_but_keeps_quality_filters():
    ctx = FilterContext(
        frozenset(),
        active_only=True,
        require_nome_fantasia=False,
        require_telefone=False,
        require_email=True,
        block_backoffice_email=True,
    )
    good = {
        "cnpj": "12345678000190",
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "6201501",
        "correio_eletronico": "diretoria@empresa.com.br",
    }
    assert matches_estabelecimento(good, ctx)
    assert not matches_estabelecimento({**good, "correio_eletronico": "nfe@empresa.com.br"}, ctx)


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


def test_email_filter_accepts_free_provider_and_blocks_backoffice():
    assert has_eligible_email({"correio_eletronico": "dono@gmail.com"})
    assert has_eligible_email({"correio_eletronico": "vendas@empresa.com.br"})
    assert not has_eligible_email({"correio_eletronico": "nfe@empresa.com.br"})
    assert not has_eligible_email({"correio_eletronico": "fiscal.loja@hotmail.com"})


def test_requires_valid_email_when_enabled():
    ctx = FilterContext(
        frozenset(["4751201"]),
        require_email=True,
        require_nome_fantasia=False,
        require_telefone=False,
    )
    item = {
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
        "correio_eletronico": "responsavel@hotmail.com",
    }
    assert matches_estabelecimento(item, ctx)
    item["correio_eletronico"] = "financeiro@empresa.com.br"
    assert not matches_estabelecimento(item, ctx)


def test_minimum_activity_age():
    today = date(2026, 9, 22)
    assert has_minimum_activity_age({"data_inicio_atividade": "20250922"}, 12, today=today)
    assert not has_minimum_activity_age({"data_inicio_atividade": "20251001"}, 12, today=today)


def test_storage_funnel_keeps_headquarters_only():
    ctx = FilterContext(
        frozenset(["4751201"]),
        headquarters_only=True,
        require_nome_fantasia=False,
        require_telefone=False,
    )
    item = {
        "cnpj": "12345678000200",
        "identificador_matriz_filial": "2",
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
    }
    assert not matches_estabelecimento(item, ctx)
    item["identificador_matriz_filial"] = "1"
    assert matches_estabelecimento(item, ctx)


def test_storage_funnel_skips_recently_decided_cnpj():
    cnpj = "12345678000100"
    ctx = FilterContext(
        frozenset(["4751201"]),
        excluded_cnpjs=frozenset([cnpj]),
        require_nome_fantasia=False,
        require_telefone=False,
    )
    item = {
        "cnpj": cnpj,
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
    }
    assert not matches_estabelecimento(item, ctx)


def test_candidate_budget_stops_new_companies_but_keeps_selected_one():
    ctx = FilterContext(
        frozenset(["4751201"]),
        max_candidates=1,
        require_nome_fantasia=False,
        require_telefone=False,
    )
    first = {
        "cnpj": "12345678000190",
        "cnpj_basico": "12345678",
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
    }
    second = {
        "cnpj": "99999999000190",
        "cnpj_basico": "99999999",
        "situacao_cadastral": "02",
        "cnae_fiscal_principal": "4751201",
    }

    assert matches_estabelecimento(first, ctx)
    track_estabelecimento(first, ctx)
    assert matches_estabelecimento(first, ctx)
    assert not matches_estabelecimento(second, ctx)
