from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()


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
    download_chunk_bytes: int = int(os.getenv("DOWNLOAD_CHUNK_BYTES", "1048576"))
    timeout: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "120"))
    include_types: frozenset[str] = frozenset(
        filter(None, os.getenv("INCLUDE_TYPES", "").split(","))
    )
    keep_downloads: bool = os.getenv("KEEP_DOWNLOADS", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
