"""Enriquecimento de presença digital pós-ETL (site, plataforma, redes, decisores)."""

from __future__ import annotations

import logging
import os
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

USER_AGENT = "cnpj-etl-enricher/1.0 (+https://github.com/chatbobo22-coder/prospect-etl-inejctor)"
FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "yahoo.com",
        "yahoo.com.br",
        "icloud.com",
        "bol.com.br",
        "uol.com.br",
        "terra.com.br",
        "ig.com.br",
        "msn.com",
        "proton.me",
        "protonmail.com",
        "ymail.com",
    }
)

PLATFORM_PATTERNS: dict[str, list[str]] = {
    "shopify": [r"cdn\.shopify\.com", r"Shopify\.theme", r"myshopify\.com", r"shopify-section"],
    "nuvemshop": [r"nuvemshop", r"tiendanube", r"lojavirtualnuvem", r"mitiendanube"],
    "tray": [r"tray\.com\.br", r"traycdn", r"traycorp"],
    "vtex": [r"vtexassets", r"vteximg", r"__vtex", r"vtexcommercestable", r"vtex\.com"],
    "woocommerce": [r"woocommerce", r"wp-content/plugins/woocommerce", r"/wc-api/"],
    "loja_integrada": [r"lojaintegrada\.com\.br", r"cdn\.lojaintegrada", r"instaclose"],
}

ADMIN_QUALIFICATIONS = frozenset({"05", "16", "17", "49"})


@dataclass
class EnrichSettings:
    batch_size: int = int(os.getenv("ENRICH_BATCH_SIZE", "300"))
    request_timeout: int = int(os.getenv("ENRICH_REQUEST_TIMEOUT", "15"))
    delay_seconds: float = float(os.getenv("ENRICH_DELAY_SECONDS", "0.5"))
    brasilapi_enabled: bool = os.getenv("ENRICH_BRASILAPI", "false").lower() in {"1", "true", "yes"}
    external_api_key: str = os.getenv("ENRICH_EXTERNAL_API_KEY", "").strip()
    external_api_provider: str = os.getenv("ENRICH_EXTERNAL_API_PROVIDER", "").strip().lower()


@dataclass
class EnrichResult:
    cnpj: str
    cnpj_basico: str
    email_original: str | None = None
    email_dominio: str | None = None
    email_tipo: str | None = None
    site_url: str | None = None
    site_ativo: bool = False
    site_http_status: int | None = None
    site_titulo: str | None = None
    plataforma: str | None = None
    plataformas_detectadas: list[str] = field(default_factory=list)
    plataforma_confianca: int = 0
    instagram_url: str | None = None
    whatsapp_url: str | None = None
    linkedin_url: str | None = None
    decisor_nome: str | None = None
    decisor_qualificacao: str | None = None
    faixa_faturamento_estimada: str | None = None
    faturamento_fonte: str | None = None
    digital_score: int = 0
    digital_maturity: str = "offline"
    sinais: dict[str, Any] = field(default_factory=dict)
    enrich_status: str = "pending"
    enrich_error: str | None = None


def classify_email(email: str | None) -> tuple[str | None, str | None, str]:
    if not email or "@" not in email:
        return None, None, "invalido"
    email = email.strip().lower()
    domain = email.rsplit("@", 1)[1]
    if domain in FREE_EMAIL_DOMAINS:
        return domain, email, "gratuito"
    return domain, email, "corporativo"


def domain_resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, None)
        return True
    except socket.gaierror:
        return False


def site_candidates(email_domain: str | None, email_tipo: str) -> list[str]:
    if not email_domain or email_tipo != "corporativo":
        return []
    if not domain_resolves(email_domain):
        return []
    # HTTPS primeiro; HTTP só como fallback (evita timeouts longos)
    return [
        f"https://{email_domain}/",
        f"https://www.{email_domain}/",
        f"http://{email_domain}/",
    ]


def _http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    return session


def probe_site_url(
    session: requests.Session, url: str, timeout: int
) -> tuple[str | None, int | None, str, str, str | None]:
    connect_timeout = min(5, timeout)
    try:
        response = session.get(
            url,
            timeout=(connect_timeout, timeout),
            allow_redirects=True,
        )
        html = response.text[:500_000]
        title_match = re.search(r"<title[^>]*>([^<]{1,200})</title>", html, re.I)
        title = title_match.group(1).strip() if title_match else ""
        return response.url, response.status_code, title, html, None
    except requests.exceptions.SSLError as exc:
        if url.startswith("https://"):
            http_url = "http://" + url.removeprefix("https://")
            try:
                response = session.get(
                    http_url,
                    timeout=(connect_timeout, timeout),
                    allow_redirects=True,
                )
                html = response.text[:500_000]
                title_match = re.search(r"<title[^>]*>([^<]{1,200})</title>", html, re.I)
                title = title_match.group(1).strip() if title_match else ""
                return response.url, response.status_code, title, html, f"ssl_fallback:{exc.__class__.__name__}"
            except requests.RequestException as inner:
                return None, None, "", "", str(inner)[:200]
        return None, None, "", "", str(exc)[:200]
    except requests.RequestException as exc:
        return None, None, "", "", str(exc)[:200]


def detect_platforms(html: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for name, patterns in PLATFORM_PATTERNS.items():
        hits = sum(1 for pattern in patterns if re.search(pattern, html, re.I))
        if hits:
            confidence = min(100, 40 + hits * 20)
            found.append((name, confidence))
    found.sort(key=lambda item: item[1], reverse=True)
    return found


def extract_social_links(html: str, base_url: str | None) -> dict[str, str | None]:
    links = {"instagram": None, "whatsapp": None, "linkedin": None}
    for match in re.finditer(r"""href=["']([^"']+)["']""", html, re.I):
        href = match.group(1)
        if base_url and href.startswith("/"):
            href = urljoin(base_url, href)
        lower = href.lower()
        if "instagram.com/" in lower and not links["instagram"]:
            links["instagram"] = href.split("?", 1)[0]
        elif ("wa.me/" in lower or "api.whatsapp.com/" in lower) and not links["whatsapp"]:
            links["whatsapp"] = href.split("?", 1)[0]
        elif "linkedin.com/" in lower and not links["linkedin"]:
            links["linkedin"] = href.split("?", 1)[0]
    return links


def estimate_revenue_band(
    *,
    porte: str | None,
    opcao_mei: str | None,
    opcao_simples: str | None,
    capital_social,
) -> tuple[str, str]:
    if opcao_mei == "S":
        return "até R$ 81 mil/ano (MEI)", "heuristica_mei"
    if porte == "01":
        return "até R$ 360 mil/ano (Microempresa)", "heuristica_porte"
    if porte == "03":
        return "R$ 360 mil – R$ 4,8 mi/ano (EPP)", "heuristica_porte"
    if porte == "05":
        return "acima de R$ 4,8 mi/ano (Demais)", "heuristica_porte"
    if opcao_simples == "S":
        return "Simples Nacional (faixa variável)", "heuristica_simples"
    try:
        capital = float(capital_social or 0)
    except (TypeError, ValueError):
        capital = 0
    if capital >= 10_000_000:
        return "capital social elevado (≥ R$ 10 mi)", "heuristica_capital"
    if capital >= 1_000_000:
        return "capital social médio-alto (≥ R$ 1 mi)", "heuristica_capital"
    return "não estimado (sem MEI/Simples/porte conclusivo)", "heuristica_local"


def calculate_score(result: EnrichResult) -> tuple[int, str]:
    score = 0
    if result.email_tipo == "corporativo":
        score += 15
    if result.site_ativo:
        score += 35
    if result.plataforma:
        score += 25
    if result.whatsapp_url:
        score += 10
    if result.instagram_url:
        score += 10
    if result.linkedin_url:
        score += 5
    if result.decisor_nome:
        score += 10
    if score >= 75:
        maturity = "ecommerce_confirmado"
    elif score >= 50:
        maturity = "ecommerce_provavel"
    elif score >= 25:
        maturity = "presenca_basica"
    else:
        maturity = "offline"
    return min(score, 100), maturity


def fetch_decisor(conn, cnpj_basico: str) -> tuple[str | None, str | None]:
    row = conn.execute(
        """
        SELECT nome_socio_razao_social, qualificacao_socio
        FROM cnpj.socios
        WHERE cnpj_basico = %s
        ORDER BY
          CASE WHEN qualificacao_socio = ANY(%s) THEN 0 ELSE 1 END,
          data_entrada_sociedade NULLS LAST
        LIMIT 1
        """,
        (cnpj_basico, list(ADMIN_QUALIFICATIONS)),
    ).fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def maybe_fetch_external(cnpj: str, settings: EnrichSettings, result: EnrichResult) -> None:
    if settings.external_api_key and settings.external_api_provider == "speedio":
        try:
            response = requests.get(
                "https://api-get-leads.speedio.com.br/search_enriched_leads/cnpj",
                params={"cnpj": cnpj},
                headers={"Authorization": settings.external_api_key},
                timeout=settings.request_timeout,
            )
            if response.status_code == 200:
                payload = response.json()
                item = payload[0] if isinstance(payload, list) and payload else payload
                if isinstance(item, dict):
                    if not result.site_url and item.get("website"):
                        result.site_url = item["website"]
                        result.sinais["speedio_website"] = item["website"]
                    band = item.get("faixa_faturamento_empresa") or item.get("faixa_faturamento_cnpj")
                    if band:
                        result.faixa_faturamento_estimada = str(band)
                        result.faturamento_fonte = "speedio"
                    admin = item.get("administrador")
                    if admin and not result.decisor_nome:
                        result.decisor_nome = admin
                        result.faturamento_fonte = result.faturamento_fonte or "speedio"
        except requests.RequestException as exc:
            result.sinais["speedio_error"] = str(exc)[:200]

    if settings.brasilapi_enabled:
        try:
            response = requests.get(
                f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}",
                timeout=settings.request_timeout,
            )
            if response.status_code == 200:
                payload = response.json()
                result.sinais["brasilapi_situacao"] = payload.get("descricao_situacao_cadastral")
        except requests.RequestException as exc:
            result.sinais["brasilapi_error"] = str(exc)[:200]


def enrich_record(row: dict, conn, settings: EnrichSettings) -> EnrichResult:
    result = EnrichResult(cnpj=row["cnpj"], cnpj_basico=row["cnpj_basico"])
    result.email_original = row.get("email")
    domain, normalized_email, email_tipo = classify_email(result.email_original)
    result.email_dominio = domain
    result.email_tipo = email_tipo
    result.sinais["email"] = normalized_email

    faixa, fonte = estimate_revenue_band(
        porte=row.get("porte"),
        opcao_mei=row.get("opcao_mei"),
        opcao_simples=row.get("opcao_simples"),
        capital_social=row.get("capital_social"),
    )
    result.faixa_faturamento_estimada = faixa
    result.faturamento_fonte = fonte

    decisor, qualificacao = fetch_decisor(conn, result.cnpj_basico)
    result.decisor_nome = decisor
    result.decisor_qualificacao = qualificacao

    maybe_fetch_external(result.cnpj, settings, result)

    session = _http_session()
    candidates = site_candidates(domain, email_tipo)
    if result.site_url and result.site_url not in candidates:
        candidates.insert(0, result.site_url)

    last_error = None
    for url in candidates:
        final_url, status, title, html, error = probe_site_url(session, url, settings.request_timeout)
        if error:
            last_error = error
            result.sinais.setdefault("site_probe_errors", []).append({url: error})
        if status is None:
            continue
        result.site_url = final_url or url
        result.site_http_status = status
        result.site_titulo = title[:250] if title else None
        result.site_ativo = status < 500
        if result.site_ativo and html:
            platforms = detect_platforms(html)
            if platforms:
                result.plataforma = platforms[0][0]
                result.plataforma_confianca = platforms[0][1]
                result.plataformas_detectadas = [name for name, _ in platforms]
            social = extract_social_links(html, result.site_url)
            result.instagram_url = social["instagram"]
            result.whatsapp_url = social["whatsapp"]
            result.linkedin_url = social["linkedin"]
            result.enrich_status = "done" if platforms else "partial"
            break

    if not result.site_url:
        result.enrich_status = "no_site"
    elif not result.site_ativo:
        result.enrich_status = "failed"
        result.enrich_error = last_error
    elif result.enrich_status == "pending":
        result.enrich_status = "partial"

    if not result.whatsapp_url and row.get("telefone_1"):
        digits = re.sub(r"\D", "", row["telefone_1"])
        if len(digits) >= 10:
            result.sinais["telefone_candidato_whatsapp"] = f"https://wa.me/55{digits}"

    result.digital_score, result.digital_maturity = calculate_score(result)
    return result


def upsert_result(conn, result: EnrichResult) -> None:
    conn.execute(
        """
        INSERT INTO cnpj.digital_presenca (
            cnpj, cnpj_basico, email_original, email_dominio, email_tipo,
            site_url, site_ativo, site_http_status, site_titulo,
            plataforma, plataformas_detectadas, plataforma_confianca,
            instagram_url, whatsapp_url, linkedin_url,
            decisor_nome, decisor_qualificacao,
            faixa_faturamento_estimada, faturamento_fonte,
            digital_score, digital_maturity, sinais,
            enrich_status, enrich_error, enriched_at, updated_at
        ) VALUES (
            %(cnpj)s, %(cnpj_basico)s, %(email_original)s, %(email_dominio)s, %(email_tipo)s,
            %(site_url)s, %(site_ativo)s, %(site_http_status)s, %(site_titulo)s,
            %(plataforma)s, %(plataformas_detectadas)s, %(plataforma_confianca)s,
            %(instagram_url)s, %(whatsapp_url)s, %(linkedin_url)s,
            %(decisor_nome)s, %(decisor_qualificacao)s,
            %(faixa_faturamento_estimada)s, %(faturamento_fonte)s,
            %(digital_score)s, %(digital_maturity)s, %(sinais)s,
            %(enrich_status)s, %(enrich_error)s, now(), now()
        )
        ON CONFLICT (cnpj) DO UPDATE SET
            email_original = EXCLUDED.email_original,
            email_dominio = EXCLUDED.email_dominio,
            email_tipo = EXCLUDED.email_tipo,
            site_url = EXCLUDED.site_url,
            site_ativo = EXCLUDED.site_ativo,
            site_http_status = EXCLUDED.site_http_status,
            site_titulo = EXCLUDED.site_titulo,
            plataforma = EXCLUDED.plataforma,
            plataformas_detectadas = EXCLUDED.plataformas_detectadas,
            plataforma_confianca = EXCLUDED.plataforma_confianca,
            instagram_url = EXCLUDED.instagram_url,
            whatsapp_url = EXCLUDED.whatsapp_url,
            linkedin_url = EXCLUDED.linkedin_url,
            decisor_nome = EXCLUDED.decisor_nome,
            decisor_qualificacao = EXCLUDED.decisor_qualificacao,
            faixa_faturamento_estimada = EXCLUDED.faixa_faturamento_estimada,
            faturamento_fonte = EXCLUDED.faturamento_fonte,
            digital_score = EXCLUDED.digital_score,
            digital_maturity = EXCLUDED.digital_maturity,
            sinais = EXCLUDED.sinais,
            enrich_status = EXCLUDED.enrich_status,
            enrich_error = EXCLUDED.enrich_error,
            enriched_at = now(),
            updated_at = now()
        """,
        {
            **result.__dict__,
            "sinais": Jsonb(result.sinais),
        },
    )


def fetch_pending(conn, limit: int, *, force: bool = False) -> list[dict]:
    condition = "d.cnpj IS NULL OR d.enrich_status IN ('failed', 'pending')"
    if force:
        condition = "TRUE"
    rows = conn.execute(
        f"""
        SELECT
          v.cnpj,
          v.cnpj_basico,
          v.email,
          v.telefone_1,
          v.nome_fantasia,
          v.porte,
          v.opcao_mei,
          v.opcao_simples,
          v.capital_social
        FROM cnpj.v_bi_varejo v
        LEFT JOIN cnpj.digital_presenca d ON d.cnpj = v.cnpj
        WHERE {condition}
        ORDER BY v.updated_at DESC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    columns = [
        "cnpj",
        "cnpj_basico",
        "email",
        "telefone_1",
        "nome_fantasia",
        "porte",
        "opcao_mei",
        "opcao_simples",
        "capital_social",
    ]
    return [dict(zip(columns, row)) for row in rows]


def ensure_digital_presenca_schema(conn) -> None:
    row = conn.execute(
        """
        SELECT COUNT(*) = 2
        FROM information_schema.columns
        WHERE table_schema = 'cnpj'
          AND table_name = 'digital_presenca'
          AND column_name IN ('cnpj', 'cnpj_basico')
        """
    ).fetchone()[0]
    if not row:
        raise RuntimeError(
            "Schema cnpj.digital_presenca incompleto. Rode: python -m cnpj_etl.cli migrate"
        )


def run_enrichment(conn, settings: EnrichSettings | None = None, *, force: bool = False) -> dict[str, int]:
    settings = settings or EnrichSettings()
    ensure_digital_presenca_schema(conn)
    rows = fetch_pending(conn, settings.batch_size, force=force)
    stats = {"processed": 0, "done": 0, "partial": 0, "no_site": 0, "failed": 0}
    log.info("Enriquecimento digital: %s registros na fila", len(rows))
    for row in rows:
        try:
            result = enrich_record(row, conn, settings)
            upsert_result(conn, result)
            conn.commit()
            stats["processed"] += 1
            status_key = result.enrich_status if result.enrich_status in stats else "partial"
            stats[status_key] = stats.get(status_key, 0) + 1
            log.info(
                "%s score=%s plataforma=%s site=%s status=%s",
                result.cnpj,
                result.digital_score,
                result.plataforma or "-",
                result.site_url or "-",
                result.enrich_status,
            )
        except Exception as exc:
            conn.rollback()
            stats["failed"] += 1
            log.exception("Falha ao enriquecer %s: %s", row["cnpj"], exc)
        time.sleep(settings.delay_seconds)
    return stats
