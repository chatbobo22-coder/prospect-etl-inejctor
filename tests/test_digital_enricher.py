from cnpj_etl.digital_enricher import (
    EnrichResult,
    calculate_score,
    classify_email,
    detect_platforms,
    estimate_revenue_band,
    extract_social_links,
)


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
    html = '''
      <a href="https://instagram.com/minhaloja">IG</a>
      <a href="https://wa.me/5511999998888">Zap</a>
    '''
    links = extract_social_links(html, "https://loja.com.br")
    assert links["instagram"] == "https://instagram.com/minhaloja"
    assert links["whatsapp"] == "https://wa.me/5511999998888"


def test_estimate_mei():
    faixa, fonte = estimate_revenue_band(
        porte="01", opcao_mei="S", opcao_simples="N", capital_social="1000"
    )
    assert "MEI" in faixa
    assert fonte == "heuristica_mei"


def test_digital_score_ecommerce():
    result = EnrichResult(
        cnpj="123",
        cnpj_basico="12345678",
        email_tipo="corporativo",
        site_ativo=True,
        plataforma="shopify",
        whatsapp_url="https://wa.me/5511",
        decisor_nome="João",
    )
    score, maturity = calculate_score(result)
    assert score >= 75
    assert maturity == "ecommerce_confirmado"
