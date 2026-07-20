"""Filtros de carga para reduzir volume (CNAE + situação cadastral)."""

from dataclasses import dataclass, field

# Comércio varejista — segmento solicitado
DEFAULT_FILTER_CNAES = (
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
)

ACTIVE_STATUS = "02"

FILTER_FILE_TYPES = frozenset(
    {
        "Cnaes",
        "Municipios",
        "Paises",
        "Naturezas",
        "Qualificacoes",
        "Motivos",
        "Estabelecimentos",
        "Empresas",
        "Simples",
        "Socios",
    }
)

FILE_LOAD_ORDER = {
    "Cnaes": 1,
    "Municipios": 2,
    "Paises": 3,
    "Naturezas": 4,
    "Qualificacoes": 5,
    "Motivos": 6,
    "Estabelecimentos": 10,
    "Empresas": 20,
    "Simples": 30,
    "Socios": 40,
}


@dataclass
class FilterContext:
    cnaes: frozenset[str]
    active_only: bool = True
    matched_basics: set[str] = field(default_factory=set)

    @property
    def enabled(self) -> bool:
        return bool(self.cnaes)

    def sort_key(self, file_type: str) -> tuple[int, str]:
        return (FILE_LOAD_ORDER.get(file_type, 99), file_type)


def parse_secondary_cnaes(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip() for part in value.split(",") if len(part.strip()) == 7}


def matches_estabelecimento(item: dict, ctx: FilterContext) -> bool:
    if ctx.active_only and item.get("situacao_cadastral") != ACTIVE_STATUS:
        return False
    if not ctx.cnaes:
        return True
    principal = item.get("cnae_fiscal_principal") or ""
    if principal in ctx.cnaes:
        return True
    return bool(parse_secondary_cnaes(item.get("cnaes_fiscais_secundarios")) & ctx.cnaes)


def should_load_row(kind: str, item: dict, ctx: FilterContext | None) -> bool:
    if not ctx or not ctx.enabled:
        return True
    if kind == "Estabelecimentos":
        return matches_estabelecimento(item, ctx)
    if kind == "Cnaes":
        return (item.get("codigo") or "") in ctx.cnaes
    if kind in {"Empresas", "Simples", "Socios"}:
        return (item.get("cnpj_basico") or "") in ctx.matched_basics
    return True


def track_estabelecimento(item: dict, ctx: FilterContext) -> None:
    basic = item.get("cnpj_basico")
    if basic:
        ctx.matched_basics.add(basic)
