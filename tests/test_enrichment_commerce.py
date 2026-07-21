from cnpj_etl.enrichment.commerce import (
    analyze_page_html,
    detect_platforms,
    has_strong_commerce_from_flags,
)
from cnpj_etl.enrichment.models import EnrichResult
from cnpj_etl.enrichment.scoring import apply_all_scores, calculate_commerce_score


def test_site_instagram_whatsapp_not_ecommerce():
    result = EnrichResult(
        cnpj="1",
        cnpj_basico="12345678",
        site_valid=True,
        site_reachable=True,
        instagram_url="https://instagram.com/loja",
        whatsapp_valid=True,
        whatsapp_url="https://wa.me/5511999999999",
        email_tipo="corporativo",
    )
    apply_all_scores(result)
    assert result.commerce_maturity != "ecommerce_confirmado"


def test_woocommerce_alone_not_confirmed():
    html = '<link rel="stylesheet" href="/wp-content/plugins/woocommerce/assets/w.css">'
    signals = analyze_page_html(html)
    assert signals.plataforma == "woocommerce"
    score, maturity = calculate_commerce_score(
        EnrichResult(
            cnpj="1",
            cnpj_basico="12345678",
            plataforma=signals.plataforma,
            plataforma_confianca=signals.plataforma_confianca,
            has_product_page=signals.has_product_page,
            has_product_schema=signals.has_product_schema,
            has_price=signals.has_price,
            has_cart=signals.has_cart,
            has_checkout=signals.has_checkout,
            has_add_to_cart=signals.has_add_to_cart,
        )
    )
    assert maturity != "ecommerce_confirmado"


def test_product_price_cart_confirms():
    html = """
    <script type="application/ld+json">{"@type":"Product","offers":{"price":"99.90"}}</script>
    <button class="add-to-cart">Adicionar ao carrinho</button>
    <a href="/cart">Carrinho</a>
    """
    signals = analyze_page_html(html)
    assert has_strong_commerce_from_flags(
        has_checkout=signals.has_checkout,
        has_cart=signals.has_cart,
        has_add_to_cart=signals.has_add_to_cart,
        has_product_schema=signals.has_product_schema,
        has_price=signals.has_price,
        plataforma="shopify",
    )


def test_checkout_is_strong_signal():
    html = '<a href="/checkout">Finalizar compra</a>'
    signals = analyze_page_html(html)
    assert signals.has_checkout is True


def test_accounting_wordpress_not_confirmed():
    html = """
    <html><head><title>Contabilidade Online</title></head>
    <body>wp-content/plugins/woocommerce</body></html>
    """
    signals = analyze_page_html(html)
    score, maturity = calculate_commerce_score(
        EnrichResult(
            cnpj="1",
            cnpj_basico="12345678",
            plataforma="woocommerce",
            plataforma_confianca=45,
            has_product_page=signals.has_product_page,
            has_product_schema=signals.has_product_schema,
            has_price=signals.has_price,
            has_cart=signals.has_cart,
            has_checkout=signals.has_checkout,
            has_add_to_cart=signals.has_add_to_cart,
            transactional_signals={"coming_soon": False},
        )
    )
    assert maturity != "ecommerce_confirmado"


def test_coming_soon_reduces_commerce():
    result = EnrichResult(
        cnpj="1",
        cnpj_basico="12345678",
        plataforma="shopify",
        plataforma_confianca=95,
        has_product_schema=True,
        has_price=True,
        has_add_to_cart=True,
        has_cart=True,
        transactional_signals={"coming_soon": True},
    )
    _, maturity = calculate_commerce_score(result)
    assert maturity != "ecommerce_confirmado"


def test_detect_magento():
    html = "Mage.Cookies.data-mage-init magento"
    platforms = detect_platforms(html)
    assert platforms[0][0] == "magento"
