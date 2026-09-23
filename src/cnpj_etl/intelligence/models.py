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


@dataclass(frozen=True)
class IntelligenceSettings:
    batch_size: int = int(os.getenv("INTELLIGENCE_BATCH_SIZE", "100"))
    sources: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(
            "INTELLIGENCE_SOURCES",
            "receita,email_quality,website,rdap,cvm,gdelt",
        )
    )
    request_timeout: int = int(os.getenv("INTELLIGENCE_REQUEST_TIMEOUT", "15"))
    max_rounds: int = int(os.getenv("INTELLIGENCE_MAX_ROUNDS", "40"))
    delay_seconds: float = float(os.getenv("INTELLIGENCE_DELAY_SECONDS", "0.25"))
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
