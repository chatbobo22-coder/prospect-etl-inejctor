from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from .filters import DEFAULT_FILTER_CNAES, FILTER_FILE_TYPES, normalize_cnae

load_dotenv()


def _env_flag(name: str, default: str = "false") -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        raw = default
    return raw.lower() in {"1", "true", "yes", "on"}


def _parse_filter_cnaes() -> frozenset[str]:
    if _env_flag("DISABLE_FILTERS"):
        return frozenset()
    raw = os.getenv("FILTER_CNAES")
    if raw is None or not raw.strip():
        return DEFAULT_FILTER_CNAES
    raw = raw.strip()
    if raw.lower() in {"none", "off", "false", "*", "all"}:
        return frozenset()
    return frozenset(
        normalized
        for part in raw.split(",")
        if (normalized := normalize_cnae(part))
    )


def _parse_filter_ufs() -> frozenset[str]:
    raw = os.getenv("FILTER_UF", "").strip()
    if not raw:
        return frozenset()
    return frozenset(part.strip().upper() for part in raw.split(",") if part.strip())


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url.removeprefix("postgres://")
    if "supabase.co" in url and "sslmode=" not in url:
        url = f"{url}&sslmode=require" if "?" in url else f"{url}?sslmode=require"
    return url


def resolve_database_url() -> str:
    explicit = os.getenv("DATABASE_URL", "").strip()
    if explicit:
        return _normalize_database_url(explicit)

    for key in ("POSTGRES_URL_NON_POOLING", "POSTGRES_URL", "POSTGRES_PRISMA_URL"):
        url = os.getenv(key, "").strip()
        if url:
            return _normalize_database_url(url)

    host = os.getenv("POSTGRES_HOST")
    password = os.getenv("POSTGRES_PASSWORD")
    if host and password:
        user = os.getenv("POSTGRES_USER", "postgres")
        database = os.getenv("POSTGRES_DATABASE", "postgres")
        user_info = f"{quote(user, safe='')}:{quote(password, safe='')}"
        return f"postgresql://{user_info}@{host}:5432/{database}?sslmode=require"

    if os.getenv("GITHUB_ACTIONS") == "true":
        raise RuntimeError(
            "DATABASE_URL não configurado no GitHub Actions. "
            "Adicione em Settings → Secrets and variables → Actions → New repository secret."
        )

    return "postgresql://cnpj:cnpj@localhost:5432/cnpj"


@dataclass(frozen=True)
class Settings:
    database_url: str = resolve_database_url()
    base_url: str = os.getenv(
        "RFB_BASE_URL",
        "https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9",
    )
    data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "25000"))
    log_progress_every: int = int(os.getenv("LOG_PROGRESS_EVERY", "50000"))
    download_chunk_bytes: int = int(os.getenv("DOWNLOAD_CHUNK_BYTES", "1048576"))
    timeout: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "120"))
    include_types: frozenset[str] = frozenset(
        filter(None, os.getenv("INCLUDE_TYPES", "").split(","))
    )
    keep_downloads: bool = _env_flag("KEEP_DOWNLOADS")
    filter_cnaes: frozenset[str] = field(default_factory=_parse_filter_cnaes)
    filter_active_only: bool = _env_flag("FILTER_ACTIVE_ONLY", "true")
    filter_include_secondary_cnae: bool = _env_flag("FILTER_CNAE_INCLUDE_SECONDARY", "false")
    filter_ufs: frozenset[str] = field(default_factory=_parse_filter_ufs)

    def filters_enabled(self) -> bool:
        return bool(self.filter_cnaes)

    def resolved_file_types(self) -> frozenset[str]:
        if self.include_types:
            return self.include_types
        if self.filter_cnaes:
            return FILTER_FILE_TYPES
        return frozenset()
