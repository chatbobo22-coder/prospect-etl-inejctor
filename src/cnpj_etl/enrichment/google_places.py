"""Integração opcional com Google Places API (New)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import requests

from .models import EnrichResult, EnrichSettings
from .website import normalize_text

log = logging.getLogger(__name__)

PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

TEXT_SEARCH_FIELDS = "places.id,places.displayName,places.formattedAddress,places.location"
DETAILS_FIELDS = (
    "id,displayName,formattedAddress,nationalPhoneNumber,websiteUri,"
    "businessStatus,rating,userRatingCount,googleMapsUri"
)


class GooglePlacesClient:
    def __init__(self, settings: EnrichSettings):
        self.settings = settings
        self.requests_made = 0

    @property
    def enabled(self) -> bool:
        return self.settings.google_places_enabled and bool(self.settings.google_places_api_key)

    def _headers(self, field_mask: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.settings.google_places_api_key,
            "X-Goog-FieldMask": field_mask,
        }

    def search(self, text_query: str) -> list[dict]:
        if not self.enabled or self.requests_made >= self.settings.google_places_max_requests:
            return []
        payload = {"textQuery": text_query, "languageCode": "pt-BR"}
        try:
            response = requests.post(
                PLACES_TEXT_SEARCH_URL,
                json=payload,
                headers=self._headers(TEXT_SEARCH_FIELDS),
                timeout=self.settings.google_places_timeout,
            )
            self.requests_made += 1
            if response.status_code != 200:
                log.warning("Google Places search HTTP %s", response.status_code)
                return []
            return response.json().get("places", [])
        except requests.RequestException:
            log.exception("Google Places search failed")
            return []

    def details(self, place_id: str) -> dict | None:
        if not self.enabled or self.requests_made >= self.settings.google_places_max_requests:
            return None
        try:
            response = requests.get(
                PLACES_DETAILS_URL.format(place_id=place_id),
                headers=self._headers(DETAILS_FIELDS),
                timeout=self.settings.google_places_timeout,
            )
            self.requests_made += 1
            if response.status_code != 200:
                return None
            return response.json()
        except requests.RequestException:
            log.exception("Google Places details failed")
            return None


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def score_place_match(
    place: dict,
    *,
    nome_fantasia: str | None,
    razao_social: str | None,
    municipio: str | None,
    uf: str | None,
    telefone: str | None,
    endereco: str | None,
    cep: str | None,
    cnae: str | None,
) -> int:
    score = 0
    display = (place.get("displayName") or {}).get("text", "")
    address = place.get("formattedAddress", "")

    if nome_fantasia and _similarity(display, nome_fantasia) >= 0.75:
        score += 30
    elif razao_social and _similarity(display, razao_social) >= 0.70:
        score += 20

    municipio_norm = normalize_text(municipio)
    uf_norm = (uf or "").upper()
    addr_norm = normalize_text(address)
    if municipio_norm and municipio_norm in addr_norm:
        score += 10
    if uf_norm and re.search(rf"\b{uf_norm.lower()}\b", addr_norm):
        score += 10

    if endereco and normalize_text(endereco)[:20] in addr_norm:
        score += 10
    if cep and cep.replace("-", "") in re.sub(r"\D", "", address):
        score += 10

    place_phone = re.sub(r"\D", "", place.get("nationalPhoneNumber", "") or "")
    tel = re.sub(r"\D", "", telefone or "")
    if place_phone and tel and place_phone[-10:] == tel[-10:]:
        score += 15

    # Penalidades
    if municipio_norm and municipio_norm not in addr_norm:
        score -= 40

    # CNAE category check reservado — sem mapeamento confiável aqui
    _ = cnae

    return max(0, min(100, score))


def should_use_cached(checked_at: datetime | None, ttl_days: int) -> bool:
    if not checked_at:
        return False
    return checked_at > datetime.now(timezone.utc) - timedelta(days=ttl_days)


def enrich_from_google_places(
    result: EnrichResult,
    settings: EnrichSettings,
    client: GooglePlacesClient | None = None,
    *,
    cached_checked_at: datetime | None = None,
    cached_place_id: str | None = None,
) -> None:
    client = client or GooglePlacesClient(settings)
    if not client.enabled:
        return
    if cached_place_id and should_use_cached(cached_checked_at, settings.google_places_ttl_days):
        result.google_place_id = cached_place_id
        result.sinais["google_places_cache"] = True
        return

    query_parts = [
        p
        for p in (result.nome_fantasia, result.razao_social, result.municipio_descricao, result.uf)
        if p
    ]
    if not query_parts:
        return
    text_query = " ".join(query_parts)
    candidates = client.search(text_query)
    if not candidates:
        result.retry_reason = result.retry_reason or "google_places_no_result"
        return

    scored: list[tuple[int, dict]] = []
    for place in candidates:
        match = score_place_match(
            place,
            nome_fantasia=result.nome_fantasia,
            razao_social=result.razao_social,
            municipio=result.municipio_descricao,
            uf=result.uf,
            telefone=result.telefone_1,
            endereco=result.logradouro,
            cep=result.cep,
            cnae=result.cnae_fiscal_principal,
        )
        scored.append((match, place))
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    result.google_place_match_score = best_score
    if best_score < settings.google_places_min_match:
        result.sinais["google_places_best_score"] = best_score
        return

    place_id = best.get("id") or best.get("name", "").split("/")[-1]
    details = client.details(place_id) if place_id else None
    payload = details or best

    result.google_place_id = place_id
    result.google_place_name = (payload.get("displayName") or {}).get("text")
    result.google_place_address = payload.get("formattedAddress")
    result.google_place_phone = payload.get("nationalPhoneNumber")
    result.google_place_website = payload.get("websiteUri")
    result.google_business_status = payload.get("businessStatus")
    result.google_rating = payload.get("rating")
    result.google_rating_count = payload.get("userRatingCount")
    result.google_maps_url = payload.get("googleMapsUri")
    result.sinais["google_places_requests"] = client.requests_made

    if result.google_place_website and best_score >= settings.google_places_min_match:
        result.site_url = result.google_place_website
        result.site_source = "google_places"
        result.sinais["site_from_google_places"] = True
