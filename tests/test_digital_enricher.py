from unittest.mock import MagicMock

from cnpj_etl.digital_enricher import count_domain_shared, extract_social_links
from cnpj_etl.enrichment.models import EnrichResult
from cnpj_etl.enrichment.scoring import calculate_score, estimate_revenue_band
from cnpj_etl.enrichment.email import classify_email
from cnpj_etl.enrichment.commerce import detect_platforms


def test_classify_corporate_email():
    domain, email, tipo = classify_email("Contato@MinhaLoja.com.br")
    assert domain == "minhaloja.com.br"
    assert tipo == "corporativo"
    assert email == "contato@minhaloja.com.br"


def test_classify_free_email():
    domain, _, tipo = classify_email("loja@gmail.com")
    assert domain == "gmail.com"
    assert tipo == "gratuito"


def test_detect_shopify():
    html = '<script src="https://cdn.shopify.com/s/files/1/theme.js"></script>'
    platforms = detect_platforms(html)
    assert platforms[0][0] == "shopify"


def test_detect_vtex_and_nuvemshop():
    html_vtex = "window.__vtexDeviceFingerprint"
    html_nuvem = "tiendanube assets nuvemshop"
    assert detect_platforms(html_vtex)[0][0] == "vtex"
    assert detect_platforms(html_nuvem)[0][0] == "nuvemshop"


def test_extract_whatsapp_instagram():
    html = """
      <a href="https://instagram.com/minhaloja">IG</a>
      <a href="https://wa.me/5511999998888">Zap</a>
    """
    links = extract_social_links(html, "https://loja.com.br")
    assert links["instagram"] == "https://instagram.com/minhaloja"
    assert links["whatsapp"] == "https://wa.me/5511999998888"


def test_estimate_mei():
    faixa, fonte = estimate_revenue_band(
        porte="01", opcao_mei="S", opcao_simples="N", capital_social="1000"
    )
    assert "MEI" in faixa
    assert fonte == "heuristica_porte_receita"


def test_digital_score_requires_strong_commerce_for_confirmado():
    result = EnrichResult(
        cnpj="123",
        cnpj_basico="12345678",
        email_tipo="corporativo",
        site_valid=True,
        site_reachable=True,
        plataforma="shopify",
        plataforma_confianca=95,
        whatsapp_valid=True,
        whatsapp_url="https://wa.me/5511999998888",
        decisor_nome="João",
        has_product_schema=True,
        has_price=True,
        has_add_to_cart=True,
        has_cart=True,
    )
    score, maturity = calculate_score(result)
    assert result.commerce_score >= 60
    assert maturity == "ecommerce_confirmado"


def test_presence_only_not_ecommerce_confirmado():
    result = EnrichResult(
        cnpj="123",
        cnpj_basico="12345678",
        site_valid=True,
        instagram_url="https://instagram.com/loja",
        whatsapp_valid=True,
        whatsapp_url="https://wa.me/5511",
    )
    _, maturity = calculate_score(result)
    assert maturity != "ecommerce_confirmado"


def test_count_domain_shared_with_excluded_cnpj_uses_typed_comparison():
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (2,)

    count = count_domain_shared(conn, "loja.com.br", "12345678000199")

    query, params = conn.execute.call_args.args
    assert "%s IS NULL" not in query
    assert "cnpj <> %s" in query
    assert params == ("loja.com.br", "12345678000199")
    assert count == 2


def test_count_domain_shared_without_exclusion_uses_single_parameter():
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (3,)

    count = count_domain_shared(conn, "loja.com.br")

    _, params = conn.execute.call_args.args
    assert params == ("loja.com.br",)
    assert count == 3
