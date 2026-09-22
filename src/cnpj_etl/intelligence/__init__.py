"""Inteligência comercial progressiva, rastreável por fonte."""

from .models import IntelligenceSettings, Person, Signal, SourceResult
from .pipeline import get_company_profile, list_sources, run_intelligence, run_intelligence_until_empty

__all__ = [
    "IntelligenceSettings",
    "Person",
    "Signal",
    "SourceResult",
    "get_company_profile",
    "list_sources",
    "run_intelligence",
    "run_intelligence_until_empty",
]
