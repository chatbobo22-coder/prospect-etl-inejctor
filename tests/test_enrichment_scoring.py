from cnpj_etl.enrichment.models import EnrichResult
from cnpj_etl.enrichment.scoring import (
    apply_all_scores,
    calculate_confidence_score,
    calculate_lead_score,
)


def test_scores_bounded():
    result = EnrichResult(
        cnpj="1",
        cnpj_basico="12345678",
        site_valid=True,
        email_tipo="corporativo",
        whatsapp_valid=True,
        instagram_url="https://instagram.com/x",
        linkedin_url="https://linkedin.com/company/x",
        google_place_match_score=90,
        google_business_status="OPERATIONAL",
        plataforma="shopify",
        plataforma_confianca=95,
        has_product_schema=True,
        has_price=True,
        has_add_to_cart=True,
        has_cart=True,
        has_checkout=True,
        sinais={"porte": "05", "opcao_mei": "N"},
    )
    apply_all_scores(result)
    for attr in (
        "presence_score",
        "commerce_score",
        "fit_score",
        "pain_score",
        "confidence_score",
        "lead_score",
    ):
        value = getattr(result, attr)
        assert 0 <= value <= 100


def test_strong_observed_fit_can_reach_70_without_fixed_cnae_list(monkeypatch):
    monkeypatch.delenv("PROSPECT_PRIORITY_CNAES", raising=False)
    result = EnrichResult(
        cnpj="12345678000190",
        cnpj_basico="12345678",
        cnae_fiscal_principal="6201501",
        site_valid=True,
        email_tipo="corporativo",
        whatsapp_valid=True,
        has_catalog=True,
        has_product_schema=True,
        has_product_page=True,
        has_price=True,
        has_add_to_cart=True,
        has_cart=True,
        has_checkout=True,
        plataforma="shopify",
        plataforma_confianca=95,
        sinais={"porte": "05", "opcao_mei": "N", "cnpj_on_site": True},
    )

    apply_all_scores(result)

    assert result.fit_score >= 70
    assert result.confidence_score >= 70
    assert result.lead_score >= 70


def test_lead_score_weights():
    result = EnrichResult(
        cnpj="1", cnpj_basico="1", fit_score=80, pain_score=60, confidence_score=40
    )
    lead, _ = calculate_lead_score(result)
    assert lead == round(80 * 0.40 + 60 * 0.35 + 40 * 0.25)


def test_low_confidence_score():
    result = EnrichResult(
        cnpj="1",
        cnpj_basico="1",
        site_match_status="rejected_webmail",
        site_validation_reasons=["webmail_domain"],
    )
    assert calculate_confidence_score(result) <= 0
