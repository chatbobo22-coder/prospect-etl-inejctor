"""Tipos e configuração da esteira de inteligência."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from typing import Any


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(v.strip().lower() for v in os.getenv(name, default).split(",") if v.strip())
    )


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _configured_sources() -> tuple[str, ...]:
    explicit = os.getenv("INTELLIGENCE_SOURCES")
    if explicit is not None:
        return _csv_env("INTELLIGENCE_SOURCES", "")
    sources = ["receita", "email_quality", "website", "rdap", "cvm", "gdelt"]
    for source, key_name in (
        ("apollo", "APOLLO_API_KEY"),
        ("prospeo", "PROSPEO_API_KEY"),
        ("hunter", "HUNTER_API_KEY"),
    ):
        if os.getenv(key_name, "").strip():
            sources.append(source)
    return tuple(sources)


@dataclass(frozen=True)
class IntelligenceSettings:
    batch_size: int = int(os.getenv("INTELLIGENCE_BATCH_SIZE", "100"))
    workers: int = max(1, int(os.getenv("INTELLIGENCE_WORKERS", "1")))
    concurrent_sources: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(
            "INTELLIGENCE_CONCURRENT_SOURCES",
            "email_quality,website,rdap",
        )
    )
    sources: tuple[str, ...] = field(default_factory=_configured_sources)
    request_timeout: int = int(os.getenv("INTELLIGENCE_REQUEST_TIMEOUT", "15"))
    max_rounds: int = int(os.getenv("INTELLIGENCE_MAX_ROUNDS", "40"))
    delay_seconds: float = float(os.getenv("INTELLIGENCE_DELAY_SECONDS", "0.25"))
    profile_commit_batch_size: int = max(
        1, int(os.getenv("INTELLIGENCE_COMMIT_BATCH_SIZE", "50"))
    )
    min_lead_score: int = int(
        os.getenv(
            "INTELLIGENCE_ENTRY_MIN_SCORE",
            os.getenv("INTELLIGENCE_MIN_LEAD_SCORE", "35"),
        )
    )
    website_max_pages: int = int(os.getenv("INTELLIGENCE_WEBSITE_MAX_PAGES", "3"))
    gdelt_max_records: int = int(os.getenv("GDELT_MAX_RECORDS", "10"))
    gdelt_timeout_seconds: int = int(os.getenv("GDELT_TIMEOUT_SECONDS", "12"))
    gdelt_batch_size: int = int(os.getenv("GDELT_BATCH_SIZE", "25"))
    gdelt_delay_seconds: float = float(os.getenv("GDELT_DELAY_SECONDS", "5.2"))
    gdelt_circuit_breaker_errors: int = int(
        os.getenv("GDELT_CIRCUIT_BREAKER_ERRORS", "3")
    )
    pagespeed_api_key: str = os.getenv("PAGESPEED_API_KEY", "").strip()
    provider_api_key: str = os.getenv("INTELLIGENCE_PROVIDER_API_KEY", "").strip()
    apollo_api_key: str = os.getenv("APOLLO_API_KEY", "").strip()
    prospeo_api_key: str = os.getenv("PROSPEO_API_KEY", "").strip()
    hunter_api_key: str = os.getenv("HUNTER_API_KEY", "").strip()
    provider_people_limit: int = int(os.getenv("PEOPLE_PROVIDER_LIMIT", "3"))
    provider_batch_size: int = int(os.getenv("PEOPLE_PROVIDER_BATCH_SIZE", "10"))
    reveal_provider_emails: bool = field(
        default_factory=lambda: _bool_env("PEOPLE_PROVIDER_REVEAL_EMAILS", True)
    )
    reveal_provider_phones: bool = field(
        default_factory=lambda: _bool_env("PEOPLE_PROVIDER_REVEAL_PHONES", False)
    )


@dataclass
class Person:
    full_name: str
    relationship_type: str = "employee"
    role_title: str | None = None
    linkedin_url: str | None = None
    business_email: str | None = None
    business_phone: str | None = None
    is_decision_maker: bool = False
    confidence: int = 0
    source_url: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Signal:
    signal_type: str
    category: str
    title: str
    score: int
    confidence: int
    description: str | None = None
    observed_at: datetime | None = None
    expires_at: datetime | None = None
    source_url: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceResult:
    source_code: str
    people: list[Person] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "success"
