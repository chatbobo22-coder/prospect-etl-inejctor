from cnpj_etl.prospect import evaluate_qualification, select_contact_channel


def _base_row(**overrides):
    row = {
        "lead_score": 75,
        "confidence_score": 70,
        "commerce_maturity": "ecommerce_confirmado",
        "site_valid": True,
        "site_reachable": True,
        "site_match_status": "validated",
        "site_validation_reasons": [],
        "decisor_nome": "Maria Silva",
        "telefone_1": "11999998888",
        "email": "contato@loja.com.br",
        "email_tipo": "corporativo",
        "whatsapp_valid": True,
        "whatsapp_number_normalized": "5511999998888",
        "whatsapp_url": "https://wa.me/5511999998888",
        "instagram_url": None,
        "plataforma": "shopify",
    }
    row.update(overrides)
    return row


def test_qualified_with_whatsapp_confirmed():
    status, rejection, reasons = evaluate_qualification(_base_row())
    assert status == "qualified"
    assert not rejection
    assert "email_corporativo" in reasons or "whatsapp_confirmado" in reasons


def test_strong_lead_without_decisor():
    status, rejection, _ = evaluate_qualification(_base_row(decisor_nome=""))
    assert status == "qualified"
    assert "sem_decisor" not in str(rejection)


def test_instagram_not_automatic_outreach():
    status, rejection, _ = evaluate_qualification(
        _base_row(
            whatsapp_valid=False,
            whatsapp_url=None,
            whatsapp_number_normalized=None,
            telefone_1="",
            email="",
            email_tipo="gratuito",
            instagram_url="https://instagram.com/loja",
        )
    )
    assert status in {"rejected", "review_required"}
    assert any("instagram" in r for r in rejection)


def test_low_confidence_rejected(monkeypatch):
    monkeypatch.setenv("PROSPECT_MIN_CONFIDENCE_SCORE", "80")
    status, rejection, _ = evaluate_qualification(_base_row(confidence_score=50))
    assert status == "rejected"
    assert any(r.startswith("confidence_baixa") for r in rejection)


def test_fiscal_email_not_preferred_channel():
    channel, _, conf, role = select_contact_channel(
        {
            "email": "fiscal@contador.com.br",
            "email_tipo": "corporativo",
            "telefone_1": "1133334444",
            "whatsapp_valid": False,
        }
    )
    assert channel == "telefone_comercial"
    assert role == "general"
    assert conf >= 50


def test_whatsapp_candidate_not_confirmed():
    channel, _, conf, _ = select_contact_channel(
        {
            "telefone_1": "",
            "whatsapp_valid": False,
            "telefone_candidato_whatsapp": "https://wa.me/5511999998888",
        }
    )
    assert channel == "whatsapp_candidato"
    assert conf < 50
