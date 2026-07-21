"""Detecção de plataforma e-commerce e sinais transacionais."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from .models import EnrichSettings
from .website import safe_fetch, validate_url_safe

PLATFORM_PATTERNS: dict[str, list[str]] = {
    "shopify": [r"cdn\.shopify\.com", r"Shopify\.theme", r"myshopify\.com", r"shopify-section"],
    "nuvemshop": [r"nuvemshop", r"tiendanube", r"lojavirtualnuvem", r"mitiendanube"],
    "tray": [r"tray\.com\.br", r"traycdn", r"traycorp"],
    "vtex": [r"vtexassets", r"vteximg", r"__vtex", r"vtexcommercestable", r"vtex\.com"],
    "woocommerce": [
        r"woocommerce",
        r"wp-content/plugins/woocommerce",
        r"/wc-api/",
        r"add-to-cart",
    ],
    "loja_integrada": [r"lojaintegrada\.com\.br", r"cdn\.lojaintegrada", r"instaclose"],
    "magento": [r"Mage\.Cookies", r"/static/version", r"magento", r"data-mage-init"],
    "wake": [r"wake\.com\.br", r"cdn\.wake", r"wakecommerce"],
    "dooca": [r"dooca", r"doocacommerce", r"dcstore"],
    "bagy": [r"bagy\.com\.br", r"cdn\.bagy"],
    "opencart": [r"route=product/product", r"catalog/view/theme", r"index\.php\?route="],
    "prestashop": [r"prestashop", r"/modules/ps_", r"var prestashop"],
    "wix_stores": [r"wixstores", r"wix-ecommerce", r"static\.wixstatic\.com"],
    "squarespace_commerce": [r"squarespace\.com", r"static1\.squarespace", r"commerce.squarespace"],
}

CHAT_PROVIDERS: dict[str, list[str]] = {
    "blip": [r"blip\.chat", r"take\.net/blip"],
    "zenvia": [r"zenvia", r"totalvoice"],
    "jivochat": [r"jivosite", r"jivo\.chat"],
    "tawk": [r"tawk\.to"],
    "zendesk": [r"static\.zdassets", r"zendesk"],
    "intercom": [r"intercomcdn", r"intercom\.io"],
    "crisp": [r"client\.crisp\.chat"],
    "hubspot": [r"js\.hs-scripts", r"hubspot"],
    "drift": [r"js\.driftt\.com"],
    "livechat": [r"livechatinc\.com"],
    "freshchat": [r"freshchat", r"wchat\.freshchat"],
}

INTERNAL_PATHS = (
    "/produtos",
    "/produto",
    "/loja",
    "/shop",
    "/catalogo",
    "/categorias",
    "/carrinho",
    "/cart",
    "/checkout",
    "/contato",
    "/products",
    "/product",
    "/p/",
)

COMING_SOON_PATTERNS = re.compile(
    r"em breve|coming soon|under construction|loja em construção|site em manutenção",
    re.I,
)


@dataclass
class CommerceSignals:
    has_product_page: bool = False
    has_product_schema: bool = False
    has_price: bool = False
    has_cart: bool = False
    has_checkout: bool = False
    has_add_to_cart: bool = False
    has_catalog: bool = False
    has_search: bool = False
    has_customer_login: bool = False
    has_chat: bool = False
    chat_provider: str | None = None
    has_contact_form: bool = False
    plataforma: str | None = None
    plataformas_detectadas: list[str] = field(default_factory=list)
    plataforma_confianca: int = 0
    coming_soon: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def detect_platforms(html: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for name, patterns in PLATFORM_PATTERNS.items():
        hits = [p for p in patterns if re.search(p, html, re.I)]
        if not hits:
            continue
        if len(hits) >= 3:
            confidence = 95
        elif len(hits) >= 2:
            confidence = 75
        else:
            confidence = 45
        found.append((name, confidence))
    found.sort(key=lambda item: item[1], reverse=True)
    return found


def _detect_chat(html: str) -> tuple[bool, str | None]:
    for provider, patterns in CHAT_PROVIDERS.items():
        if any(re.search(p, html, re.I) for p in patterns):
            return True, provider
    if re.search(r"chat-widget|live-chat|open-chat|btn-chat", html, re.I):
        return True, "custom"
    return False, None


def analyze_page_html(html: str, page_url: str | None = None) -> CommerceSignals:
    signals = CommerceSignals()
    lower = html.lower()

    if COMING_SOON_PATTERNS.search(html):
        signals.coming_soon = True

    platforms = detect_platforms(html)
    if platforms:
        signals.plataforma = platforms[0][0]
        signals.plataforma_confianca = platforms[0][1]
        signals.plataformas_detectadas = [name for name, _ in platforms]

    if re.search(r'"@type"\s*:\s*"Product"', html, re.I) or re.search(
        r"application/ld\+json[^>]*>[^<]*Product", html, re.I
    ):
        signals.has_product_schema = True

    if re.search(r'property="og:type"\s+content="product"', html, re.I):
        signals.has_product_schema = True

    if re.search(r"/produto/|/products/|/product/|/p/\d", lower):
        signals.has_product_page = True

    if re.search(
        r"(add-to-cart|adicionar ao carrinho|comprar agora|buy now|btn-comprar)",
        lower,
    ):
        signals.has_add_to_cart = True

    if re.search(r"/cart|/carrinho|minicart|shopping-cart", lower):
        signals.has_cart = True

    if re.search(r"/checkout|finalizar compra|checkout-button|place-order", lower):
        signals.has_checkout = True

    if re.search(r'class="[^"]*product[^"]*".*?(R\$|\bprice\b|\bpreço\b)', html, re.I | re.S):
        signals.has_price = True
    elif re.search(r"R\$\s?\d{1,3}(?:\.\d{3})*,\d{2}", html):
        signals.has_price = True

    if re.search(r"/categor|/catalog|product-grid|grade-produtos|lista-produtos", lower):
        signals.has_catalog = True

    if re.search(r'type="search"|name="q"|buscar produto|search-form', lower):
        signals.has_search = True

    if re.search(r"login|minha conta|customer/account|entrar", lower):
        signals.has_customer_login = True

    if re.search(r"<form[^>]+(contato|contact|fale conosco)", lower):
        signals.has_contact_form = True

    signals.has_chat, signals.chat_provider = _detect_chat(html)
    return signals


def merge_commerce_signals(base: CommerceSignals, other: CommerceSignals) -> CommerceSignals:
    merged = CommerceSignals(
        has_product_page=base.has_product_page or other.has_product_page,
        has_product_schema=base.has_product_schema or other.has_product_schema,
        has_price=base.has_price or other.has_price,
        has_cart=base.has_cart or other.has_cart,
        has_checkout=base.has_checkout or other.has_checkout,
        has_add_to_cart=base.has_add_to_cart or other.has_add_to_cart,
        has_catalog=base.has_catalog or other.has_catalog,
        has_search=base.has_search or other.has_search,
        has_customer_login=base.has_customer_login or other.has_customer_login,
        has_chat=base.has_chat or other.has_chat,
        has_contact_form=base.has_contact_form or other.has_contact_form,
        coming_soon=base.coming_soon or other.coming_soon,
        extra={**base.extra, **other.extra},
    )
    merged.chat_provider = base.chat_provider or other.chat_provider
    all_platforms = list(dict.fromkeys(base.plataformas_detectadas + other.plataformas_detectadas))
    merged.plataformas_detectadas = all_platforms
    confidences = [base.plataforma_confianca, other.plataforma_confianca]
    if all_platforms:
        merged.plataforma = base.plataforma or other.plataforma
        merged.plataforma_confianca = max(confidences)
    return merged


def crawl_transactional_pages(
    session,
    start_url: str,
    settings: EnrichSettings,
) -> CommerceSignals:
    parsed = urlparse(start_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    aggregate = CommerceSignals()
    visited: set[str] = set()
    queue = [start_url]

    for path in INTERNAL_PATHS:
        candidate = urljoin(base, path)
        if candidate not in queue:
            queue.append(candidate)

    pages_fetched = 0
    for url in queue:
        if pages_fetched >= settings.crawler_max_pages:
            break
        if url in visited:
            continue
        visited.add(url)
        ok, _ = validate_url_safe(url)
        if not ok:
            continue
        fetch = safe_fetch(session, url, settings)
        if not fetch.html:
            continue
        pages_fetched += 1
        page_signals = analyze_page_html(fetch.html, url)
        aggregate = merge_commerce_signals(aggregate, page_signals)
        time.sleep(settings.crawler_delay)
    return aggregate


def has_strong_commerce_signal(signals: CommerceSignals) -> bool:
    if signals.has_checkout:
        return True
    if signals.has_cart and signals.has_add_to_cart:
        return True
    if signals.has_product_schema and signals.has_price and signals.plataforma:
        return True
    return False


def has_strong_commerce_from_flags(
    *,
    has_checkout: bool,
    has_cart: bool,
    has_add_to_cart: bool,
    has_product_schema: bool,
    has_price: bool,
    plataforma: str | None,
) -> bool:
    if has_checkout:
        return True
    if has_cart and has_add_to_cart:
        return True
    if has_product_schema and has_price and plataforma:
        return True
    return False


def extract_instagram_link(html: str, base_url: str | None) -> str | None:
    for match in re.finditer(r"""href=["']([^"']+)["']""", html, re.I):
        href = match.group(1)
        if base_url and href.startswith("/"):
            href = urljoin(base_url, href)
        if "instagram.com/" in href.lower():
            return href.split("?", 1)[0]
    return None


def extract_linkedin_link(html: str, base_url: str | None) -> str | None:
    for match in re.finditer(r"""href=["']([^"']+)["']""", html, re.I):
        href = match.group(1)
        if base_url and href.startswith("/"):
            href = urljoin(base_url, href)
        if "linkedin.com/" in href.lower():
            return href.split("?", 1)[0]
    return None
