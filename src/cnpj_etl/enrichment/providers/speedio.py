"""Provider Speedio."""

from __future__ import annotations

import requests

from ..models import EnrichResult, EnrichSettings
from .base import ExternalProvider


class SpeedioProvider(ExternalProvider):
    def enrich(self, cnpj: str, result: EnrichResult, settings: EnrichSettings) -> None:
        if not settings.external_api_key:
            return
        try:
            response = requests.get(
                "https://api-get-leads.speedio.com.br/search_enriched_leads/cnpj",
                params={"cnpj": cnpj},
                headers={"Authorization": settings.external_api_key},
                timeout=settings.request_timeout,
            )
            if response.status_code != 200:
                result.sinais["speedio_error"] = f"http_{response.status_code}"
                return
            payload = response.json()
            item = payload[0] if isinstance(payload, list) and payload else payload
            if not isinstance(item, dict):
                return
            if not result.site_url and item.get("website"):
                result.site_url = item["website"]
                result.site_source = "external_provider"
                result.sinais["speedio_website"] = item["website"]
            band = item.get("faixa_faturamento_empresa") or item.get("faixa_faturamento_cnpj")
            if band:
                result.faturamento_estimado = str(band)
                result.faturamento_estimado_fonte = "speedio"
            admin = item.get("administrador")
            if admin and not result.decisor_nome:
                result.decisor_nome = admin
        except requests.RequestException as exc:
            result.sinais["speedio_error"] = exc.__class__.__name__
