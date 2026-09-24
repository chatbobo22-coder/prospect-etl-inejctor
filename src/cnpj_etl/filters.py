"""Filtros de carga para reduzir volume (CNAE + situação cadastral)."""

from dataclasses import dataclass, field
from datetime import date

from .enrichment.email import is_blocked_outreach_email, is_valid_email

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
    # O Simples precisa estar disponível antes da razão social para que o
    # primeiro lote de empresas já possa excluir MEI e seguir ao funil rápido.
    "Simples": 20,
    "Empresas": 30,
    "Socios": 40,
}


@dataclass
class FilterContext:
    cnaes: frozenset[str]
    apply_filters: bool = True
    active_only: bool = True
    ufs: frozenset[str] = field(default_factory=frozenset)
    include_secondary_cnae: bool = False
    require_nome_fantasia: bool = True
    require_telefone: bool = True
    require_email: bool = False
    block_backoffice_email: bool = True
    min_activity_months: int = 0
    min_population: int = 0
    headquarters_only: bool = False
    max_candidates: int = 0
    allowed_municipios: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    excluded_cnpjs: frozenset[str] = field(default_factory=frozenset)
    selected_cnpjs: set[str] = field(default_factory=set)
    matched_basics: set[str] = field(default_factory=set)

    @property
    def enabled(self) -> bool:
        return self.apply_filters

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


def has_eligible_email(item: dict, *, block_backoffice: bool = True) -> bool:
    email = item.get("correio_eletronico") or item.get("email")
    if not is_valid_email(email):
        return False
    return not block_backoffice or not is_blocked_outreach_email(email)


def has_minimum_activity_age(item: dict, months: int, *, today: date | None = None) -> bool:
    if months <= 0:
        return True
    raw = item.get("data_inicio_atividade")
    if not raw:
        return False
    try:
        started = raw if isinstance(raw, date) else date.fromisoformat(str(raw).strip())
    except ValueError:
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        if len(digits) != 8:
            return False
        try:
            started = date(int(digits[:4]), int(digits[4:6]), int(digits[6:]))
        except ValueError:
            return False
    current = today or date.today()
    age_months = (current.year - started.year) * 12 + current.month - started.month
    if current.day < started.day:
        age_months -= 1
    return age_months >= months


def municipio_key(item: dict) -> tuple[str, str]:
    uf = (item.get("uf") or "").upper()
    codigo = (item.get("municipio") or "").strip().zfill(4)
    return uf, codigo


def matches_estabelecimento(item: dict, ctx: FilterContext) -> bool:
    cnpj = item.get("cnpj") or ""
    if cnpj in ctx.excluded_cnpjs:
        return False
    if (
        ctx.max_candidates > 0
        and cnpj not in ctx.selected_cnpjs
        and len(ctx.selected_cnpjs) >= ctx.max_candidates
    ):
        return False
    if ctx.headquarters_only and item.get("identificador_matriz_filial") != "1":
        return False
    if ctx.active_only and item.get("situacao_cadastral") != ACTIVE_STATUS:
        return False
    if ctx.ufs and (item.get("uf") or "").upper() not in ctx.ufs:
        return False
    if ctx.min_population > 0 and ctx.allowed_municipios:
        if municipio_key(item) not in ctx.allowed_municipios:
            return False
    if ctx.require_nome_fantasia and not has_nome_fantasia(item):
        return False
    if ctx.require_telefone and not has_valid_telefone(item):
        return False
    if ctx.require_email and not has_eligible_email(
        item, block_backoffice=ctx.block_backoffice_email
    ):
        return False
    if not has_minimum_activity_age(item, ctx.min_activity_months):
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
        if not ctx.cnaes:
            return True
        return normalize_cnae(item.get("codigo")) in ctx.cnaes
    if kind in {"Empresas", "Simples", "Socios"}:
        return (item.get("cnpj_basico") or "") in ctx.matched_basics
    return True


def track_estabelecimento(item: dict, ctx: FilterContext) -> None:
    cnpj = item.get("cnpj")
    if cnpj:
        ctx.selected_cnpjs.add(cnpj)
    basic = item.get("cnpj_basico")
    if basic:
        ctx.matched_basics.add(basic)
