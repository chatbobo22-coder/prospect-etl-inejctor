from cnpj_etl.enrichment.whatsapp import (
    extract_whatsapp_candidate,
    normalize_brazil_phone,
    phone_to_whatsapp_candidate,
)


def test_wa_me_extraction():
    result = extract_whatsapp_candidate("https://wa.me/5511999998888")
    assert result["valid"] is True
    assert result["normalized"] == "5511999998888"
    assert result["canonical_url"] == "https://wa.me/5511999998888"


def test_api_whatsapp_phone_query():
    result = extract_whatsapp_candidate("https://api.whatsapp.com/send?phone=5511988887777&text=Oi")
    assert result["valid"] is True
    assert result["normalized"] == "5511988887777"


def test_web_whatsapp_phone_query():
    result = extract_whatsapp_candidate("https://web.whatsapp.com/send?phone=5521987654321")
    assert result["normalized"] == "5521987654321"


def test_generic_link_without_number_invalid():
    result = extract_whatsapp_candidate("https://api.whatsapp.com/send")
    assert result["detected"] is True
    assert result["valid"] is False
    assert result["number"] is None


def test_cnpj_phone_only_candidate():
    candidate = phone_to_whatsapp_candidate("11999998888")
    assert candidate == "https://wa.me/5511999998888"
    result = extract_whatsapp_candidate(candidate)
    assert result["valid"] is True


def test_short_number_rejected():
    assert normalize_brazil_phone("1234") is None


def test_invalid_ddd_rejected():
    assert normalize_brazil_phone("0010119999888") is None


def test_no_duplicate_55_prefix():
    assert normalize_brazil_phone("5511999998888") == "5511999998888"
