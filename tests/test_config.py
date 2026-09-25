import importlib


def _reload_config(monkeypatch, **env):
    for key in (
        "DISABLE_FILTERS",
        "FILTER_CNAES",
        "FILTER_ACTIVE_ONLY",
        "FILTER_CNAE_INCLUDE_SECONDARY",
        "FILTER_UF",
        "FILTER_MIN_POPULATION",
        "FILTER_REQUIRE_NOME_FANTASIA",
        "FILTER_REQUIRE_TELEFONE",
        "FILTER_MAX_CANDIDATES_PER_RUN",
        "RAW_STAGING_MAX_ROWS",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import cnpj_etl.config as config

    return importlib.reload(config)


def test_empty_filter_cnaes_means_all_cnaes(monkeypatch):
    config = _reload_config(monkeypatch, FILTER_CNAES="")
    assert config._parse_filter_cnaes() == frozenset()
    assert config.Settings().filters_enabled()


def test_unset_filter_cnaes_means_all_cnaes(monkeypatch):
    config = _reload_config(monkeypatch)
    assert config._parse_filter_cnaes() == frozenset()
    assert config.Settings().filters_enabled()


def test_disable_filters(monkeypatch):
    config = _reload_config(monkeypatch, DISABLE_FILTERS="true")
    assert config._parse_filter_cnaes() == frozenset()
    assert not config.Settings().filters_enabled()


def test_empty_filter_active_only_defaults_true(monkeypatch):
    config = _reload_config(monkeypatch, FILTER_ACTIVE_ONLY="")
    assert config.Settings().filter_active_only is True


def test_min_population_default(monkeypatch):
    config = _reload_config(monkeypatch)
    assert config._parse_min_population() == 0


def test_min_population_disabled(monkeypatch):
    config = _reload_config(monkeypatch, FILTER_MIN_POPULATION="0")
    assert config._parse_min_population() == 0


def test_national_and_broad_contact_defaults(monkeypatch):
    config = _reload_config(monkeypatch)
    settings = config.Settings()
    assert settings.filter_ufs == frozenset()
    assert settings.filter_include_secondary_cnae is True
    assert settings.filter_require_nome_fantasia is False
    assert settings.filter_require_telefone is False
    assert settings.filter_max_candidates_per_run == 100000
    assert settings.raw_staging_max_rows == 25000
