"""Interface base para providers externos."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import EnrichResult, EnrichSettings


class ExternalProvider(ABC):
    @abstractmethod
    def enrich(self, cnpj: str, result: EnrichResult, settings: EnrichSettings) -> None:
        raise NotImplementedError
