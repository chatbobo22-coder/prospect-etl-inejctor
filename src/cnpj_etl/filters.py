"""Filtros de carga para reduzir volume (CNAE + situação cadastral)."""

from dataclasses import dataclass, field

ACTIVE_STATUS = "02"

# Lista exata solicitada — usada quando FILTER_CNAES não está definido
DEFAULT_FILTER_CNAES = frozenset(
    {
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
)

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
    ufs: frozenset[str] = field(default_factory=frozenset)
    include_secondary_cnae: bool = False
    require_nome_fantasia: bool = True
    require_telefone: bool = True
    matched_basics: set[str] = field(default_factory=set)

    @property
    def enabled(self) -> bool:
        return bool(self.cnaes)

    def sort_key(self, file_type: str) -> tuple[int, str]:
        return (FILE_LOAD_ORDER.get(file_type, 99), file_type)


def normalize_cnae(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.isdigit() and len(raw) < 7:
        return raw.zfill(7)
    return raw


def parse_secondary_cnaes(value: str | None) -> set[str]:
    if not value:
        return set()
    return {
        normalized
        for part in value.split(",")
        if (normalized := normalize_cnae(part)) and len(normalized) == 7
    }


def has_nome_fantasia(item: dict) -> bool:
    return bool((item.get("nome_fantasia") or "").strip())


def has_valid_telefone(item: dict) -> bool:
    ddd = "".join(ch for ch in (item.get("ddd1") or "") if ch.isdigit())
    phone = "".join(ch for ch in (item.get("telefone1") or "") if ch.isdigit())
    if len(ddd) != 2 or len(phone) < 8:
        return False
    if set(phone) == {"0"}:
        return False
    return True


def matches_estabelecimento(item: dict, ctx: FilterContext) -> bool:
    if ctx.active_only and item.get("situacao_cadastral") != ACTIVE_STATUS:
        return False
    if ctx.ufs and (item.get("uf") or "").upper() not in ctx.ufs:
        return False
    if ctx.require_nome_fantasia and not has_nome_fantasia(item):
        return False
    if ctx.require_telefone and not has_valid_telefone(item):
        return False
    if not ctx.cnaes:
        return True
    principal = normalize_cnae(item.get("cnae_fiscal_principal"))
    if principal not in ctx.cnaes:
        if not ctx.include_secondary_cnae:
            return False
        if not (parse_secondary_cnaes(item.get("cnaes_fiscais_secundarios")) & ctx.cnaes):
            return False
    return True


def should_load_row(kind: str, item: dict, ctx: FilterContext | None) -> bool:
    if not ctx or not ctx.enabled:
        return True
    if kind == "Estabelecimentos":
        return matches_estabelecimento(item, ctx)
    if kind == "Cnaes":
        return normalize_cnae(item.get("codigo")) in ctx.cnaes
    if kind in {"Empresas", "Simples", "Socios"}:
        return (item.get("cnpj_basico") or "") in ctx.matched_basics
    return True


def track_estabelecimento(item: dict, ctx: FilterContext) -> None:
    basic = item.get("cnpj_basico")
    if basic:
        ctx.matched_basics.add(basic)
