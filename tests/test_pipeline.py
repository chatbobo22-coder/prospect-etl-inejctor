from pathlib import Path
from types import SimpleNamespace

from cnpj_etl.pipeline import (
    build_filter_context,
    discard_unresolved_staging,
    seed_staged_candidates,
)


class RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    @property
    def rowcount(self):
        return len(self.rows)


class FakeConnection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []
        self.committed = False

    def execute(self, query, params=None):
        self.calls.append((query, params))
        return RowsResult(self.rows)

    def commit(self):
        self.committed = True


def _settings(**overrides):
    values = {
        "filter_cnaes": frozenset(),
        "filter_active_only": True,
        "filter_ufs": frozenset(),
        "filter_include_secondary_cnae": True,
        "filter_require_nome_fantasia": False,
        "filter_require_telefone": False,
        "filter_require_email": True,
        "filter_block_backoffice_email": True,
        "filter_min_activity_months": 12,
        "filter_min_population": 0,
        "filter_headquarters_only": True,
        "filter_max_candidates_per_run": 100_000,
        "raw_staging_max_rows": 25_000,
        "filters_enabled": lambda: True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_storage_cap_limits_raw_candidate_peak():
    context = build_filter_context(_settings(), FakeConnection())

    assert context.max_candidates == 25_000


def test_existing_raw_backlog_is_selected_before_new_downloads():
    context = build_filter_context(_settings(), FakeConnection())
    connection = FakeConnection(
        [("12345678000190", "12345678"), ("87654321000109", "87654321")]
    )

    selected = seed_staged_candidates(connection, context)

    assert selected == 2
    assert context.selected_cnpjs == {"12345678000190", "87654321000109"}
    assert context.matched_basics == {"12345678", "87654321"}
    assert connection.calls[0][1] == (25_000,)
    assert "prospectos_qualificados" in connection.calls[0][0]
    assert "candidate_decisions" in connection.calls[0][0]


def test_materialized_raw_data_is_explicitly_discarded():
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "cnpj_etl"
        / "retention.py"
    ).read_text(encoding="utf-8")

    assert "DELETE FROM cnpj.estabelecimentos" in source
    assert "FROM cnpj.prospectos_qualificados p WHERE p.cnpj=item.cnpj" in source
    assert "INSERT INTO etl.processed_candidates" in source
    assert "SELECT 1 FROM cnpj.estabelecimentos e WHERE e.cnpj_basico=item.cnpj_basico" in source
    assert "TRUNCATE cnpj.socios,cnpj.simples,cnpj.empresas,cnpj.estabelecimentos" in source


def test_unresolved_raw_staging_is_discarded_after_company_scan():
    context = build_filter_context(_settings(), FakeConnection())
    context.matched_basics.update({"12345678", "87654321"})
    context.resolved_company_basics.add("12345678")
    connection = FakeConnection([("deleted",)])

    removed = discard_unresolved_staging(connection, context)

    assert removed == 1
    assert connection.calls[0][1] == (["87654321"],)
    assert "DELETE FROM cnpj.estabelecimentos" in connection.calls[0][0]
    assert connection.committed is True
