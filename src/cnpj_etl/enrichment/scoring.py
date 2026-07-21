"""Scores separados v2."""

from __future__ import annotations

import os

from .models import EnrichResult, SCORE_VERSION


def _clamp(value: int) -> int:
    return max(0, min(100, value))


def calculate_presence_score(result: EnrichResult) -> tuple[int, str]:
    score = 0
    if result.site_valid:
        score += 30
    elif result.site_reachable:
        score += 10
    if result.email_tipo == "corporativo":
        score += 15
    if result.instagram_url:
        score += 15
    if result.whatsapp_valid:
        score += 20
    elif result.whatsapp_detected and not result.whatsapp_valid:
        score += 3
    if result.linkedin_url:
        score += 10
    if result.google_place_match_score >= 70 and result.google_business_status == "OPERATIONAL":
        score += 10
    score = _clamp(score)
    if score >= 70:
        maturity = "presenca_multicanal"
    elif score >= 45:
        maturity = "presenca_ativa"
    elif score >= 20:
        maturity = "presenca_basica"
    else:
        maturity = "offline"
    return score, maturity


def calculate_commerce_score(result: EnrichResult) -> tuple[int, str]:
    score = 0
    if result.plataforma_confianca >= 80:
        score += 20
    elif result.plataforma_confianca >= 50:
        score += 10
    elif result.plataforma:
        score += 5
    if result.has_product_schema:
        score += 20
    if result.has_product_page:
        score += 15
    if result.has_price:
        score += 10
    if result.has_add_to_cart:
        score += 15
    if result.has_cart:
        score += 10
    if result.has_checkout:
        score += 10
    if result.has_catalog and not result.has_checkout:
        score += 5

    strong = (
        result.has_checkout
        or (result.has_cart and result.has_add_to_cart)
        or (result.has_product_schema and result.has_price and bool(result.plataforma))
    )
    coming_soon = result.transactional_signals.get("coming_soon", False)
    if coming_soon:
        score = min(score, 25)

    score = _clamp(score)
    if score >= 60 and strong:
        maturity = "ecommerce_confirmado"
    elif score >= 60:
        maturity = "ecommerce_provavel"
    elif score >= 40:
        maturity = "ecommerce_provavel"
    elif score >= 20:
        maturity = "ecommerce_indicio"
    else:
        maturity = "sem_ecommerce"
    return score, maturity


def calculate_fit_score(result: EnrichResult) -> int:
    score = 0
    priority_cnaes = os.getenv("PROSPECT_PRIORITY_CNAES", "").split(",")
    priority = {c.strip() for c in priority_cnaes if c.strip()}
    if result.cnae_fiscal_principal and result.cnae_fiscal_principal in priority:
        score += 30
    elif result.cnae_fiscal_principal:
        score += 10
    porte = result.sinais.get("porte")
    if porte == "03":
        score += 15
    elif porte == "05":
        score += 20
    if result.commerce_maturity == "ecommerce_confirmado":
        score += 20
    elif result.commerce_maturity == "ecommerce_provavel":
        score += 10
    if result.has_catalog:
        score += 10
    mei_penalty = int(os.getenv("PROSPECT_MEI_PENALTY", "0"))
    if result.sinais.get("opcao_mei") == "S" and mei_penalty:
        score -= mei_penalty
    return _clamp(score)


def calculate_pain_score(result: EnrichResult) -> int:
    score = 0
    if result.whatsapp_valid and not result.has_chat:
        score += 20
    if result.site_valid and not result.has_chat:
        score += 15
    if result.commerce_score >= 40 and not result.has_chat:
        score += 20
    if result.has_contact_form and not result.has_chat:
        score += 10
    channels = sum(
        1
        for flag in (
            result.whatsapp_valid,
            bool(result.instagram_url),
            result.email_tipo == "corporativo",
            result.site_valid,
        )
        if flag
    )
    if channels >= 2 and not result.has_chat:
        score += 10
    return _clamp(score)


def calculate_confidence_score(result: EnrichResult) -> int:
    score = 0
    if result.google_place_match_score >= 70:
        score += 20
    if result.site_valid:
        score += 25
    elif result.site_reachable:
        score += 5
    tel_digits = result.sinais.get("telefone_match")
    if tel_digits:
        score += 15
    if result.email_tipo == "corporativo" and result.site_valid:
        score += 15
    if result.whatsapp_valid:
        score += 15
    if result.sinais.get("cnpj_on_site"):
        score += 20
    if result.domain_is_shared and not result.sinais.get("cnpj_on_site"):
        score -= 30
    if result.site_match_status == "rejected_webmail":
        score -= 100
    if result.whatsapp_detected and not result.whatsapp_valid:
        score -= 15
    if "title_incompativel" in " ".join(result.site_validation_reasons):
        score -= 50
    return _clamp(score)


def calculate_lead_score(result: EnrichResult) -> tuple[int, str]:
    lead = round(
        result.fit_score * 0.40 + result.pain_score * 0.35 + result.confidence_score * 0.25
    )
    lead = _clamp(lead)
    if lead >= 80:
        classification = "lead_prioritario"
    elif lead >= 60:
        classification = "lead_quente"
    elif lead >= 40:
        classification = "lead_medio"
    else:
        classification = "lead_frio"
    return lead, classification


def apply_all_scores(result: EnrichResult) -> EnrichResult:
    result.presence_score, result.presence_maturity = calculate_presence_score(result)
    result.commerce_score, result.commerce_maturity = calculate_commerce_score(result)
    result.fit_score = calculate_fit_score(result)
    result.pain_score = calculate_pain_score(result)
    result.confidence_score = calculate_confidence_score(result)
    result.lead_score, result.lead_classification = calculate_lead_score(result)
    result.score_version = SCORE_VERSION
    # Legacy deprecated fields
    result.digital_score = result.lead_score
    result.digital_maturity = result.commerce_maturity
    return result


def classify_company_size_band(
    *,
    porte: str | None,
    opcao_mei: str | None,
    opcao_simples: str | None,
    capital_social,
) -> tuple[str, str]:
    if opcao_mei == "S":
        return "faixa cadastral MEI (Receita/Simples)", "heuristica_porte_receita"
    if porte == "01":
        return "faixa cadastral Microempresa (Receita)", "heuristica_porte_receita"
    if porte == "03":
        return "faixa cadastral EPP (Receita)", "heuristica_porte_receita"
    if porte == "05":
        return "faixa cadastral Demais portes (Receita)", "heuristica_porte_receita"
    if opcao_simples == "S":
        return "Simples Nacional (faixa cadastral, não é faturamento)", "heuristica_porte_receita"
    try:
        capital = float(capital_social or 0)
    except (TypeError, ValueError):
        capital = 0
    if capital >= 10_000_000:
        return (
            "capital social cadastral elevado (proxy, não faturamento)",
            "heuristica_capital_cadastral",
        )
    if capital >= 1_000_000:
        return "capital social cadastral médio-alto (proxy)", "heuristica_capital_cadastral"
    return "porte/faturamento não estimados (apenas cadastro)", "heuristica_local"


# Compatibilidade com testes legados
def estimate_revenue_band(**kwargs) -> tuple[str, str]:
    return classify_company_size_band(**kwargs)


def calculate_score(result: EnrichResult) -> tuple[int, str]:
    apply_all_scores(result)
    return result.digital_score, result.digital_maturity
