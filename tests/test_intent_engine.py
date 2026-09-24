from datetime import datetime, timedelta, timezone

from cnpj_etl.intent.freshness import freshness_multiplier, normalized_confidence
from cnpj_etl.intent.recommendations import recommend
from cnpj_etl.intent.scoring import calculate_tironi_score, classification_for
from cnpj_etl.intent.service import _apply_preset, _deduplicate_signals


NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def test_signal_freshness_boundaries_and_structural_exception():
    assert freshness_multiplier(NOW - timedelta(days=7), "sales_hiring", NOW) == 1.0
    assert freshness_multiplier(NOW - timedelta(days=30), "sales_hiring", NOW) == 0.9
    assert freshness_multiplier(NOW - timedelta(days=90), "sales_hiring", NOW) == 0.7
    assert freshness_multiplier(NOW - timedelta(days=180), "sales_hiring", NOW) == 0.4
    assert freshness_multiplier(NOW - timedelta(days=365), "sales_hiring", NOW) == 0.2
    assert freshness_multiplier(NOW - timedelta(days=365), "crm_detected", NOW) == 1.0


def test_signal_confidence_accepts_percent_and_fraction():
    assert normalized_confidence(80) == 0.8
    assert normalized_confidence(0.75) == 0.75
    assert normalized_confidence(999) == 1.0


def test_score_is_explainable_capped_and_classified():
    company = {
        "whatsapp_valid": True,
        "whatsapp_confidence": 100,
        "has_checkout": True,
        "enriched_at": NOW,
    }
    signals = [
        {"id": 1, "signal_type": "professional_headcount", "confidence": 100,
         "observed_at": NOW, "raw_data": {"employee_count": 70}},
        {"id": 2, "signal_type": "ai_hiring", "confidence": 100,
         "observed_at": NOW, "raw_data": {}},
        {"id": 3, "signal_type": "sales_hiring", "confidence": 100,
         "observed_at": NOW, "raw_data": {}},
        {"id": 4, "signal_type": "expansion", "confidence": 100,
         "observed_at": NOW, "raw_data": {}},
        {"id": 5, "signal_type": "multiunit", "confidence": 100,
         "observed_at": NOW, "raw_data": {"active_units": 7}},
        {"id": 6, "signal_type": "paid_media", "confidence": 100,
         "observed_at": NOW, "raw_data": {}},
        {"id": 7, "signal_type": "sales_team", "confidence": 100,
         "observed_at": NOW, "raw_data": {"sellers_count": 12}},
    ]
    people = [{"role_title": "Diretor Comercial", "confidence": 100}]
    technologies = [
        {"technology": "HubSpot", "category": "crm", "confidence": 100},
        {"technology": "Bling", "category": "erp", "confidence": 100},
        {"technology": "Shopify", "category": "commerce", "confidence": 100},
    ]
    result = calculate_tironi_score(company, signals, people, technologies)
    assert result["tironi_score"] == 100
    assert result["classification"] == "PRIORIDADE COMERCIAL"
    assert result["score_breakdown"]
    assert all({"label", "impact", "confidence", "freshness"} <= item.keys()
               for item in result["score_breakdown"])


def test_classification_boundaries():
    assert classification_for(30) == "FRIO"
    assert classification_for(31) == "POTENCIAL"
    assert classification_for(51) == "QUENTE"
    assert classification_for(71) == "MUITO QUENTE"
    assert classification_for(86) == "PRIORIDADE COMERCIAL"


def test_recommendation_uses_detected_needs():
    profile = {
        "score_breakdown": [{"key": "whatsapp_sales"}, {"key": "ai_hiring"}],
        "has_sales_team": True,
        "has_crm": True,
        "has_erp": True,
        "has_ecommerce": False,
        "active_units": 2,
        "tironi_score": 82,
        "classification": "MUITO QUENTE",
        "positive_signals": ["WhatsApp comercial identificado", "contratação de IA/automação"],
    }
    result = recommend(profile, [{"full_name": "Ana", "role_title": "CEO",
                                  "is_decision_maker": True}])
    assert "ChatBô" in result["recommended_products"]
    assert "consultoria de IA" in result["recommended_products"]
    assert "Ana" in result["next_best_action"]
    assert "whatsapp" in result["sales_approach"].lower()


def test_signal_deduplication_does_not_count_same_job_twice():
    signals = [
        {"signal_type": "software_hiring", "title": "Dev Backend", "source_url": "https://x/job/1",
         "confidence": 70, "raw_data": {"job_id": "1"}},
        {"signal_type": "software_hiring", "title": "Dev Backend", "source_url": "https://mirror/job/1",
         "confidence": 95, "raw_data": {"job_id": "1"}},
    ]
    result = _deduplicate_signals(signals)
    assert len(result) == 1
    assert result[0]["confidence"] == 95


def test_search_preset_sets_filters_without_overwriting_explicit_values():
    filters = {"preset": "ecommerce_hot", "min_score": 75}
    _apply_preset(filters)
    assert filters["has_ecommerce"] is True
    assert filters["has_whatsapp"] is True
    assert filters["min_score"] == 75
