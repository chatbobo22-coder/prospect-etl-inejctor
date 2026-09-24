"""Tironi Lead Score v1: regras rastreáveis, recência e confiança."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .freshness import freshness_multiplier, normalized_confidence


@dataclass(frozen=True)
class ScoreEvidence:
    key: str
    label: str
    base_score: int
    confidence: float
    observed_at: datetime | None = None
    source_name: str | None = None
    source_url: str | None = None
    signal_id: int | None = None

    @property
    def impact(self) -> int:
        value = self.base_score * freshness_multiplier(self.observed_at, self.key) * self.confidence
        return max(0, round(value))


WEIGHTS = {
    "employees_20": (10, "20 ou mais funcionários"),
    "employees_50": (5, "50 ou mais funcionários"),
    "sellers_5": (15, "equipe com 5 ou mais vendedores"),
    "sellers_10": (5, "equipe com 10 ou mais vendedores"),
    "whatsapp_sales": (10, "WhatsApp comercial identificado"),
    "ecommerce_detected": (10, "e-commerce identificado"),
    "paid_media": (10, "aquisição digital ativa"),
    "crm_detected": (5, "CRM identificado"),
    "erp_detected": (5, "ERP identificado"),
    "sales_hiring": (10, "contratação comercial recente"),
    "software_hiring": (15, "contratação de desenvolvimento/software"),
    "ai_hiring": (20, "contratação de IA/automação"),
    "units_2": (10, "duas ou mais unidades"),
    "units_5": (5, "cinco ou mais unidades"),
    "site_improvement": (5, "oportunidade clara de melhoria no site"),
    "sales_director": (10, "diretor ou head comercial identificado"),
    "founder_ceo": (5, "CEO ou founder identificado"),
    "disconnected_stack": (10, "stack tecnológica complexa/desconectada"),
    "recent_expansion": (10, "expansão recente identificada"),
}

SIGNAL_ALIASES = {
    "whatsapp_sales": "whatsapp_sales", "paid_media": "paid_media",
    "active_ads": "paid_media", "sales_hiring": "sales_hiring",
    "commercial_expansion": "sales_hiring", "tech_hiring": "software_hiring",
    "software_hiring": "software_hiring", "ai_hiring": "ai_hiring",
    "new_branch": "recent_expansion", "expansion": "recent_expansion",
    "employee_growth": "recent_expansion", "headcount_growth": "recent_expansion",
    "site_improvement": "site_improvement", "slow_site": "site_improvement",
    "commerce_gap": "site_improvement", "no_chat": "site_improvement",
    "ecommerce_detected": "ecommerce_detected", "multiunit": "units_2",
}

CRM_RE = re.compile(r"hubspot|pipedrive|salesforce|rd station|rdstation|zoho|moskit", re.I)
ERP_RE = re.compile(r"totvs|protheus|omie|bling|sankhya|tiny|senior|sap|oracle", re.I)
ECOM_RE = re.compile(r"shopify|woocommerce|nuvemshop|tray|vtex|magento|loja integrada", re.I)
SALES_ROLE_RE = re.compile(r"diretor.*comercial|head.*comercial|commercial director|sales director", re.I)
FOUNDER_RE = re.compile(r"\b(ceo|founder|fundador|fundadora|sócio|socia|sócia)\b", re.I)


def classification_for(score: int) -> str:
    if score <= 30:
        return "FRIO"
    if score <= 50:
        return "POTENCIAL"
    if score <= 70:
        return "QUENTE"
    if score <= 85:
        return "MUITO QUENTE"
    return "PRIORIDADE COMERCIAL"


def calculate_tironi_score(
    company: dict[str, Any], signals: list[dict], people: list[dict], technologies: list[dict]
) -> dict:
    evidence: dict[str, ScoreEvidence] = {}

    def add(key: str, confidence=1.0, observed_at=None, source_name=None, source_url=None, signal_id=None):
        if key not in WEIGHTS:
            return
        item = ScoreEvidence(key, WEIGHTS[key][1], WEIGHTS[key][0], confidence, observed_at,
                             source_name, source_url, signal_id)
        current = evidence.get(key)
        if current is None or item.impact > current.impact:
            evidence[key] = item

    employees = _max_signal_number(signals, "employee_count")
    sellers = _max_signal_number(signals, "sellers_count")
    units = max(1, _max_signal_number(signals, "active_units"))
    if employees >= 20:
        add("employees_20", 0.8, source_name="professional_provider")
    if employees >= 50:
        add("employees_50", 0.8, source_name="professional_provider")
    if sellers >= 5:
        add("sellers_5", 0.8, source_name="professional_provider")
    if sellers >= 10:
        add("sellers_10", 0.8, source_name="professional_provider")
    if units >= 2:
        add("units_2", 0.9, source_name="receita")
    if units >= 5:
        add("units_5", 0.9, source_name="receita")

    for signal in signals:
        canonical = SIGNAL_ALIASES.get(str(signal.get("signal_type") or ""))
        if canonical:
            add(canonical, normalized_confidence(signal.get("confidence")),
                signal.get("observed_at"), signal.get("source_code"), signal.get("source_url"),
                signal.get("id"))

    if company.get("whatsapp_valid"):
        add("whatsapp_sales", normalized_confidence(company.get("whatsapp_confidence") or 90),
            company.get("enriched_at"), "website", company.get("site_final_url"))
    if company.get("has_checkout") or company.get("has_cart") or company.get("has_product_page"):
        add("ecommerce_detected", 0.9, company.get("enriched_at"), "website",
            company.get("site_final_url"))

    technology_names = [str(t.get("technology") or "") for t in technologies]
    joined = " ".join(technology_names)
    if CRM_RE.search(joined):
        add("crm_detected", _best_technology_confidence(technologies, CRM_RE), source_name="website")
    if ERP_RE.search(joined):
        add("erp_detected", _best_technology_confidence(technologies, ERP_RE), source_name="website")
    if ECOM_RE.search(joined):
        add("ecommerce_detected", _best_technology_confidence(technologies, ECOM_RE),
            source_name="website")
    categories = {str(t.get("category") or "").lower() for t in technologies}
    if len(categories) >= 3 or len(technology_names) >= 5:
        add("disconnected_stack", 0.7, source_name="website")

    for person in people:
        role = str(person.get("role_title") or "")
        confidence = normalized_confidence(person.get("confidence"))
        if SALES_ROLE_RE.search(role):
            add("sales_director", confidence, person.get("source_observed_at"),
                person.get("source_code"), person.get("source_url"))
        if FOUNDER_RE.search(role) or person.get("relationship_type") == "founder":
            add("founder_ceo", confidence, person.get("source_observed_at"),
                person.get("source_code"), person.get("source_url"))

    breakdown = [
        {"key": item.key, "label": item.label, "base_score": item.base_score,
         "impact": item.impact, "confidence": round(item.confidence, 2),
         "freshness": freshness_multiplier(item.observed_at, item.key),
         "source_name": item.source_name, "source_url": item.source_url,
         "signal_id": item.signal_id}
        for item in sorted(evidence.values(), key=lambda value: value.impact, reverse=True)
        if item.impact > 0
    ]
    score = min(100, sum(item["impact"] for item in breakdown))
    return {
        "tironi_score": score, "classification": classification_for(score),
        "score_breakdown": breakdown, "positive_signals": [i["label"] for i in breakdown],
        "employee_count": employees or None, "estimated_sellers_count": sellers or None,
        "active_units": units, "has_whatsapp": "whatsapp_sales" in evidence,
        "has_crm": "crm_detected" in evidence, "has_erp": "erp_detected" in evidence,
        "has_ecommerce": "ecommerce_detected" in evidence,
        "has_sales_team": sellers > 0 or "sales_director" in evidence or "sales_hiring" in evidence,
    }


def _max_signal_number(signals: list[dict], key: str) -> int:
    values = []
    for signal in signals:
        raw = signal.get("raw_data") or {}
        value = raw.get(key)
        if key == "active_units" and signal.get("signal_type") in {"active_branches", "multiunit"}:
            value = value or raw.get("branches") or raw.get("active_branches")
        if key == "employee_count" and signal.get("signal_type") == "professional_headcount":
            value = value or raw.get("employee_count")
        try:
            values.append(int(float(value)))
        except (TypeError, ValueError):
            pass
    return max(values, default=0)


def _best_technology_confidence(technologies: list[dict], pattern: re.Pattern) -> float:
    values = [normalized_confidence(t.get("confidence")) for t in technologies
              if pattern.search(str(t.get("technology") or ""))]
    return max(values, default=0.7)
