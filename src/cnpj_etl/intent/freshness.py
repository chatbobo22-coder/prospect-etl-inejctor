"""Recência e confiança dos sinais, sem depender de IA."""

from datetime import datetime, timezone

STRUCTURAL_SIGNALS = {
    "professional_headcount", "sales_team", "whatsapp_sales", "crm_detected",
    "erp_detected", "ecommerce_detected", "multiunit", "complex_operation",
    "technology_stack", "linkedin_company_presence",
}


def freshness_multiplier(observed_at: datetime | None, signal_type: str, now=None) -> float:
    if signal_type in STRUCTURAL_SIGNALS:
        return 1.0
    now = now or datetime.now(timezone.utc)
    if observed_at is None:
        return 0.2
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    days = max(0, (now - observed_at).days)
    if days <= 7:
        return 1.0
    if days <= 30:
        return 0.9
    if days <= 90:
        return 0.7
    if days <= 180:
        return 0.4
    return 0.2


def normalized_confidence(value: int | float | None) -> float:
    if value is None:
        return 0.0
    numeric = float(value)
    return max(0.0, min(1.0, numeric / 100 if numeric > 1 else numeric))
