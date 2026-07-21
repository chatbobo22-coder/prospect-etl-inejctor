"""Modelos de dados do enriquecimento v2."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

SCORE_VERSION = "v2"
ENRICHMENT_VERSION = os.getenv("ENRICHMENT_VERSION", "v2")

ALLOWED_CANDIDATE_VIEWS = frozenset(
    {
        "cnpj.v_prospect_candidates",
        "cnpj.v_bi_varejo",
    }
)

SITE_SOURCES = frozenset(
    {"google_places", "external_provider", "corporate_email_domain", "manual", "search", "unknown"}
)


@dataclass
class EnrichSettings:
    batch_size: int = int(os.getenv("ENRICH_BATCH_SIZE", "300"))
    request_timeout: int = int(os.getenv("ENRICH_REQUEST_TIMEOUT", "15"))
    connect_timeout: int = int(os.getenv("ENRICH_CONNECT_TIMEOUT", "5"))
    delay_seconds: float = float(os.getenv("ENRICH_DELAY_SECONDS", "0.5"))
    max_response_bytes: int = int(os.getenv("ENRICH_MAX_RESPONSE_BYTES", "2097152"))
    max_redirects: int = int(os.getenv("ENRICH_MAX_REDIRECTS", "5"))
    brasilapi_enabled: bool = os.getenv("ENRICH_BRASILAPI", "false").lower() in {"1", "true", "yes"}
    external_api_key: str = os.getenv("ENRICH_EXTERNAL_API_KEY", "").strip()
    external_api_provider: str = os.getenv("ENRICH_EXTERNAL_API_PROVIDER", "").strip().lower()
    candidate_view: str = os.getenv("ENRICH_CANDIDATE_VIEW", "cnpj.v_prospect_candidates")
    max_rounds: int = int(os.getenv("ENRICH_MAX_ROUNDS", "40"))
    enrichment_version: str = ENRICHMENT_VERSION
    no_site_retry_days: int = int(os.getenv("ENRICH_NO_SITE_RETRY_DAYS", "30"))
    failed_retry_hours: int = int(os.getenv("ENRICH_FAILED_RETRY_HOURS", "24"))
    crawler_max_pages: int = int(os.getenv("CRAWLER_MAX_PAGES", "8"))
    crawler_max_bytes: int = int(os.getenv("CRAWLER_MAX_BYTES_PER_PAGE", "2000000"))
    crawler_delay: float = float(os.getenv("CRAWLER_DELAY_SECONDS", "0.2"))
    google_places_enabled: bool = os.getenv("GOOGLE_PLACES_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    google_places_api_key: str = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    google_places_timeout: int = int(os.getenv("GOOGLE_PLACES_TIMEOUT", "15"))
    google_places_min_match: int = int(os.getenv("GOOGLE_PLACES_MIN_MATCH_SCORE", "70"))
    google_places_max_requests: int = int(os.getenv("GOOGLE_PLACES_MAX_REQUESTS_PER_RUN", "500"))
    google_places_ttl_days: int = int(os.getenv("GOOGLE_PLACES_TTL_DAYS", "90"))
    site_match_threshold: int = int(os.getenv("SITE_MATCH_THRESHOLD", "70"))


@dataclass
class EnrichResult:
    cnpj: str
    cnpj_basico: str
    # Email
    email_original: str | None = None
    email_dominio: str | None = None
    email_tipo: str | None = None
    # Site (legacy + v2)
    site_url: str | None = None
    site_ativo: bool = False
    site_http_status: int | None = None
    site_titulo: str | None = None
    site_source: str | None = None
    site_match_score: int = 0
    site_match_status: str | None = None
    site_reachable: bool = False
    site_valid: bool = False
    site_content_type: str | None = None
    site_final_url: str | None = None
    site_redirect_count: int = 0
    site_validation_reasons: list[str] = field(default_factory=list)
    domain_shared_count: int = 0
    domain_is_shared: bool = False
    # Platform
    plataforma: str | None = None
    plataformas_detectadas: list[str] = field(default_factory=list)
    plataforma_confianca: int = 0
    # Social
    instagram_url: str | None = None
    whatsapp_url: str | None = None
    linkedin_url: str | None = None
    whatsapp_detected: bool = False
    whatsapp_number: str | None = None
    whatsapp_number_normalized: str | None = None
    whatsapp_valid: bool = False
    whatsapp_source: str | None = None
    whatsapp_confidence: int = 0
    telefone_candidato_whatsapp: str | None = None
    # Google Places
    google_place_id: str | None = None
    google_place_match_score: int = 0
    google_place_name: str | None = None
    google_place_address: str | None = None
    google_place_phone: str | None = None
    google_place_website: str | None = None
    google_business_status: str | None = None
    google_rating: float | None = None
    google_rating_count: int | None = None
    google_maps_url: str | None = None
    # Transactional
    has_product_page: bool = False
    has_product_schema: bool = False
    has_price: bool = False
    has_cart: bool = False
    has_checkout: bool = False
    has_add_to_cart: bool = False
    has_catalog: bool = False
    has_search: bool = False
    has_customer_login: bool = False
    has_chat: bool = False
    chat_provider: str | None = None
    has_contact_form: bool = False
    transactional_signals: dict[str, Any] = field(default_factory=dict)
    # Decisor
    decisor_nome: str | None = None
    decisor_qualificacao: str | None = None
    # Porte / faturamento
    faixa_faturamento_estimada: str | None = None
    faturamento_fonte: str | None = None
    faixa_porte_receita: str | None = None
    porte_receita_fonte: str | None = None
    faturamento_estimado: str | None = None
    faturamento_estimado_fonte: str | None = None
    # Scores v2
    presence_score: int = 0
    commerce_score: int = 0
    fit_score: int = 0
    pain_score: int = 0
    confidence_score: int = 0
    lead_score: int = 0
    presence_maturity: str = "offline"
    commerce_maturity: str = "sem_ecommerce"
    lead_classification: str = "lead_frio"
    score_version: str = SCORE_VERSION
    # Legacy (deprecated)
    digital_score: int = 0
    digital_maturity: str = "offline"
    # Meta
    sinais: dict[str, Any] = field(default_factory=dict)
    enrich_status: str = "pending"
    enrich_error: str | None = None
    enrich_attempts: int = 0
    retry_reason: str | None = None
    enrichment_version: str = ENRICHMENT_VERSION
    cnae_fiscal_principal: str | None = None
    razao_social: str | None = None
    nome_fantasia: str | None = None
    uf: str | None = None
    municipio_descricao: str | None = None
    telefone_1: str | None = None
    logradouro: str | None = None
    cep: str | None = None
