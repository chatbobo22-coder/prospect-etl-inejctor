from cnpj_etl.prospect import (
    CORE_INTELLIGENCE_SOURCES,
    calculate_preliminary_score,
    calculate_reinforced_lead_score,
    classify_lead_quality,
    evaluate_qualification,
    reject_before_enrichment,
    reject_before_intelligence,
    select_contact_channel,
)


def test_preliminary_score_keeps_complete_leads_with_free_email():
    assert (
        calculate_preliminary_score(
            {
                "email": "proprietario@gmail.com",
                "telefone_1": "11999998888",
                "nome_fantasia": "Loja Forte",
                "porte": "03",
                "capital_social": 100_000,
                "cnae_fiscal_principal": "4751201",
            }
        )
        == 85
    )


def test_preliminary_score_rejects_incomplete_lead_before_http():
    assert (
        calculate_preliminary_score(
            {
                "email": "empresa@gmail.com",
                "telefone_1": "",
                "nome_fantasia": "",
                "porte": "01",
                "capital_social": 1_000,
                "cnae_fiscal_principal": "4751201",
            }
        )
        == 40
    )


def test_tironi_score_is_the_final_score_after_intelligence():
    score, bonus = calculate_reinforced_lead_score(
        {
            "lead_score": 45,
            "tironi_score": 82,
            "intelligence_presence_score": 10,
            "intelligence_capacity_score": 20,
            "intelligence_intent_score": 25,
            "intelligence_decision_makers_count": 2,
        }
    )

    assert bonus == 20
    assert score == 82


def test_cheap_prefilter_archives_rejected_contact_before_http():
    class Result:
        rowcount = 3

    class Connection:
        def __init__(self):
            self.calls = []
            self.committed = False

        def execute(self, query, params=None):
            self.calls.append((query, params))
            return Result()

        def commit(self):
            self.committed = True

    conn = Connection()
    stats = reject_before_enrichment(conn, min_pre_score=50)

    assert stats == {"rejected": 3, "threshold": 50}
    assert conn.calls[0][1] == (50, 180, 50)
    assert "pre_score_abaixo_" in conn.calls[0][0]
    assert "v.razao_social" in conn.calls[0][0]
    assert "v.telefone_1" in conn.calls[0][0]
    assert conn.committed is True


def test_public_professional_signals_reinforce_but_cap_lead_score():
    score, bonus = calculate_reinforced_lead_score(
        {
            "lead_score": 72,
            "intelligence_presence_score": 8,
            "intelligence_capacity_score": 16,
            "intelligence_intent_score": 12,
            "intelligence_decision_makers_count": 2,
        }
    )

    assert bonus == 19
    assert score == 91

    capped, capped_bonus = calculate_reinforced_lead_score(
        {
            "lead_score": 95,
            "intelligence_presence_score": 10,
            "intelligence_capacity_score": 20,
            "intelligence_intent_score": 25,
            "intelligence_decision_makers_count": 5,
        }
    )
    assert capped_bonus == 20
    assert capped == 100


def test_only_identity_and_contact_sources_block_publication():
    assert CORE_INTELLIGENCE_SOURCES == (
        "receita",
        "email_quality",
    )


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


def test_free_email_can_be_quality_a():
    row = _base_row(email="proprietario@gmail.com", email_tipo="gratuito")
    channel, _, confidence, role = select_contact_channel(row)
    assert channel == "email_gratuito"
    assert confidence >= 65
    assert role == "general"
    assert classify_lead_quality(row, channel) == "A"
    status, rejection, reasons = evaluate_qualification(row)
    assert status == "qualified"
    assert not rejection
    assert "email_gratuito" in reasons
    assert "qualidade_a" in reasons


def test_quality_b_is_qualified():
    row = _base_row(
        lead_score=72,
        confidence_score=72,
        site_valid=False,
        site_reachable=False,
        whatsapp_valid=False,
        email="dono@hotmail.com",
        email_tipo="gratuito",
    )
    channel, *_ = select_contact_channel(row)
    assert classify_lead_quality(row, channel) == "B"
    status, rejection, reasons = evaluate_qualification(row)
    assert status == "qualified"
    assert not rejection
    assert "qualidade_b" in reasons


def test_early_triage_records_ineligible_and_below_70():
    class Result:
        def __init__(self, rowcount):
            self.rowcount = rowcount

    class Connection:
        def __init__(self):
            self.calls = []
            self.committed = False

        def execute(self, query, params=None):
            self.calls.append((query, params))
            return Result(4 if len(self.calls) == 1 else 7)

        def commit(self):
            self.committed = True

    conn = Connection()
    stats = reject_before_intelligence(conn, min_lead_score=70)

    assert stats == {"ineligible": 4, "below_score": 7, "threshold": 70}
    assert conn.calls[1][1] == (70, 180, 70)
    assert "company.razao_social" in conn.calls[0][0]
    assert "e.telefone1" in conn.calls[0][0]
    assert "v.nome_fantasia" in conn.calls[1][0]
    assert "v.telefone_1" in conn.calls[1][0]
    assert "lead_score_abaixo_" in conn.calls[1][0]
    assert conn.committed is True


def test_verified_public_profile_can_be_quality_b_without_a_website():
    row = _base_row(
        lead_score=10,
        confidence_score=5,
        site_valid=False,
        site_reachable=False,
        whatsapp_valid=False,
        deliverability_status="valid",
        profile_score=22,
        data_confidence_score=8,
        intelligence_decision_makers_count=1,
        capital_social=10_000,
    )

    channel, *_ = select_contact_channel(row)
    assert classify_lead_quality(row, channel) == "B"
    status, rejection, reasons = evaluate_qualification(row)
    assert status == "qualified"
    assert not rejection
    assert "perfil_publico_verificado" in reasons


def test_public_profile_fallback_requires_valid_email():
    row = _base_row(
        lead_score=10,
        confidence_score=5,
        site_valid=False,
        whatsapp_valid=False,
        deliverability_status="risky",
        profile_score=22,
        data_confidence_score=8,
        intelligence_decision_makers_count=1,
        capital_social=100_000,
    )

    channel, *_ = select_contact_channel(row)
    assert classify_lead_quality(row, channel) is None


def test_backoffice_email_is_rejected_even_with_strong_scores():
    row = _base_row(email="nfe@empresa.com.br", email_tipo="corporativo")
    status, rejection, _ = evaluate_qualification(row)
    assert status == "rejected"
    assert "email_backoffice_bloqueado" in rejection


def test_mei_is_rejected_by_default():
    status, rejection, _ = evaluate_qualification(_base_row(opcao_mei="S"))
    assert status == "rejected"
    assert "mei_excluido" in rejection


def test_email_waits_for_technical_verification():
    status, rejection, _ = evaluate_qualification(_base_row(deliverability_status=None))
    assert status == "rejected"
    assert "email_aguardando_verificacao" in rejection


def test_risky_email_cannot_be_quality_a():
    row = _base_row(deliverability_status="risky")
    channel, *_ = select_contact_channel(row)
    assert classify_lead_quality(row, channel) == "B"


def test_strict_gate_requires_public_intelligence_profile(monkeypatch):
    monkeypatch.setenv("STRICT_INTELLIGENCE_GATE", "true")
    row = _base_row(deliverability_status="valid")
    channel, *_ = select_contact_channel(row)
    assert classify_lead_quality(row, channel) is None
    status, rejection, _ = evaluate_qualification(row)
    assert status == "rejected"
    assert "perfil_inteligencia_insuficiente" in rejection


def test_strict_gate_combines_digital_and_public_quality(monkeypatch):
    monkeypatch.setenv("STRICT_INTELLIGENCE_GATE", "true")
    row = _base_row(
        deliverability_status="valid",
        profile_quality="B",
        profile_score=68,
        data_confidence_score=8,
    )
    channel, *_ = select_contact_channel(row)
    assert classify_lead_quality(row, channel) == "B"
    status, rejection, reasons = evaluate_qualification(row)
    assert status == "qualified"
    assert not rejection
    assert "qualidade_b" in reasons
