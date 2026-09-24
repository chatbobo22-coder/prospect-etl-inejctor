"""Contratos substituíveis para integrações públicas/licenciadas futuras."""

from typing import Protocol


class CompanyProvider(Protocol):
    def find_company(self, company: dict) -> dict: ...


class PeopleProvider(Protocol):
    def find_people(self, company: dict) -> list[dict]: ...


class JobProvider(Protocol):
    def find_jobs(self, company: dict) -> list[dict]: ...


class TechnologyProvider(Protocol):
    def detect_technologies(self, company: dict) -> list[dict]: ...


class SearchProvider(Protocol):
    def search(self, query: str) -> list[dict]: ...


class NewsProvider(Protocol):
    def find_news(self, company: dict) -> list[dict]: ...


class AdsProvider(Protocol):
    def find_ads(self, company: dict) -> list[dict]: ...
