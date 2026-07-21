import importlib

from cnpj_etl.filters import DEFAULT_FILTER_CNAES


def _reload_config(monkeypatch, **env):
    for key in (
        "DISABLE_FILTERS",
        "FILTER_CNAES",
        "FILTER_ACTIVE_ONLY",
        "FILTER_CNAE_INCLUDE_SECONDARY",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import cnpj_etl.config as config

    return importlib.reload(config)


def test_empty_filter_cnaes_uses_defaults(monkeypatch):
    config = _reload_config(monkeypatch, FILTER_CNAES="")
    assert config._parse_filter_cnaes() == DEFAULT_FILTER_CNAES
    assert config.Settings().filters_enabled()


def test_unset_filter_cnaes_uses_defaults(monkeypatch):
    config = _reload_config(monkeypatch)
    assert config._parse_filter_cnaes() == DEFAULT_FILTER_CNAES


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
