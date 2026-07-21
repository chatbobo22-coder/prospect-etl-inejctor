"""HTTP seguro, SSRF protection e validação de site."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests

from .email import FREE_EMAIL_DOMAINS, is_free_email_domain
from .models import EnrichSettings

log = logging.getLogger(__name__)

USER_AGENT = "cnpj-etl-enricher/2.0 (+https://github.com/chatbobo22-coder/prospect-etl-inejctor)"

INCOMPATIBLE_TITLE_KEYWORDS = frozenset(
    {
        "contabil",
        "contador",
        "contabilidade",
        "escritorio cont",
        "provedor",
        "hosting",
        "dominio",
        "registro.br",
        "em breve",
        "coming soon",
        "under construction",
        "parked domain",
        "dominio estacionado",
    }
)

SOCIAL_REDIRECT_HOSTS = frozenset(
    {"instagram.com", "facebook.com", "linkedin.com", "twitter.com", "x.com", "youtube.com"}
)

LEGAL_SUFFIXES = re.compile(
    r"\b(ltda|ltda\.|me|epp|eireli|sa|s\.a\.|s/a|limitada|microempresa)\b",
    re.I,
)


@dataclass
class FetchResult:
    final_url: str | None = None
    status: int | None = None
    title: str = ""
    html: str = ""
    content_type: str | None = None
    reachable: bool = False
    valid: bool = False
    redirect_count: int = 0
    error: str | None = None
    retry_after_hours: int | None = None
    retry_after_days: int | None = None


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    import unicodedata

    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = LEGAL_SUFFIXES.sub(" ", text.lower())
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        return True
    if str(ip) == "169.254.169.254":
        return True
    return False


def validate_url_safe(url: str) -> tuple[bool, str | None]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, "scheme_invalido"
    if parsed.username or parsed.password:
        return False, "credenciais_na_url"
    host = parsed.hostname
    if not host:
        return False, "host_ausente"
    if host.lower() in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return False, "localhost"
    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(host, None):
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            ip = sockaddr[0]
            if _is_blocked_ip(ip):
                return False, f"ip_bloqueado:{ip}"
    except socket.gaierror:
        return False, "dns_falhou"
    return True, None


def _read_limited_body(response: requests.Response, max_bytes: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    truncated = False
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            allowed = max_bytes - (total - len(chunk))
            if allowed > 0:
                chunks.append(chunk[:allowed])
            truncated = True
            break
        chunks.append(chunk)
    return b"".join(chunks), truncated


def _is_html_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    lower = content_type.lower()
    return "text/html" in lower or "application/xhtml" in lower


def safe_fetch(
    session: requests.Session,
    url: str,
    settings: EnrichSettings,
    *,
    dns_resolver: Callable[[str], None] | None = None,
) -> FetchResult:
    result = FetchResult()
    current = url
    redirects = 0

    while redirects <= settings.max_redirects:
        ok, reason = validate_url_safe(current)
        if not ok:
            result.error = reason
            if reason == "dns_falhou":
                result.retry_after_days = 30
            return result
        if dns_resolver:
            try:
                dns_resolver(urlparse(current).hostname or "")
            except Exception:
                result.error = "dns_falhou"
                result.retry_after_days = 30
                return result

        try:
            response = session.get(
                current,
                timeout=(settings.connect_timeout, settings.request_timeout),
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            result.error = exc.__class__.__name__
            result.retry_after_hours = settings.failed_retry_hours
            return result

        status = response.status_code
        result.status = status

        if 300 <= status < 400:
            location = response.headers.get("Location")
            response.close()
            if not location:
                result.error = "redirect_sem_location"
                return result
            next_url = urljoin(current, location)
            ok, reason = validate_url_safe(next_url)
            if not ok:
                result.error = f"redirect_bloqueado:{reason}"
                return result
            current = next_url
            redirects += 1
            result.redirect_count = redirects
            continue

        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
        result.content_type = content_type
        body, _ = _read_limited_body(response, settings.max_response_bytes)
        response.close()

        if status == 429:
            retry_after = response.headers.get("Retry-After")
            result.reachable = False
            result.retry_after_hours = (
                int(retry_after) if retry_after and retry_after.isdigit() else 24
            )
            result.error = "rate_limited"
            return result

        if status in (404, 410):
            result.reachable = False
            result.valid = False
            result.retry_after_days = 30
            result.error = f"http_{status}"
            return result

        if status >= 500:
            result.reachable = False
            result.retry_after_hours = settings.failed_retry_hours
            result.error = f"http_{status}"
            return result

        if status in (401, 403):
            result.reachable = True
            result.valid = False
            result.final_url = current
            result.error = f"http_{status}"
            return result

        if not (200 <= status < 300):
            result.error = f"http_{status}"
            return result

        if not _is_html_content_type(content_type):
            result.reachable = True
            result.valid = False
            result.final_url = current
            result.error = "conteudo_nao_html"
            return result

        html = body.decode("utf-8", errors="replace")
        title_match = re.search(r"<title[^>]*>([^<]{1,200})</title>", html, re.I)
        result.final_url = current
        result.reachable = True
        result.html = html
        result.title = title_match.group(1).strip() if title_match else ""
        return result

    result.error = "max_redirects"
    return result


def domain_resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, None)
        return True
    except socket.gaierror:
        return False


def site_candidates_from_email(email_domain: str | None, email_tipo: str) -> list[str]:
    if not email_domain or email_tipo != "corporativo" or is_free_email_domain(email_domain):
        return []
    if not domain_resolves(email_domain):
        return []
    return [f"https://{email_domain}/", f"https://www.{email_domain}/"]


def extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url).hostname
    return host.lower().removeprefix("www.") if host else None


def validate_site_ownership(
    *,
    html: str,
    title: str,
    final_url: str | None,
    nome_fantasia: str | None,
    razao_social: str | None,
    municipio: str | None,
    uf: str | None,
    telefone: str | None,
    cnpj: str | None,
    email_domain: str | None,
    email_tipo: str,
    cnae: str | None,
    domain_shared_count: int = 0,
) -> tuple[int, list[str], str]:
    """Retorna (match_score, reasons, status)."""
    reasons: list[str] = []
    score = 0
    content = normalize_text(html[:100_000])
    title_norm = normalize_text(title)
    fantasia_norm = normalize_text(nome_fantasia)
    razao_norm = normalize_text(razao_social)
    municipio_norm = normalize_text(municipio)

    if email_tipo == "gratuito" or is_free_email_domain(email_domain):
        reasons.append("webmail_domain")
        return 0, reasons, "rejected_webmail"

    host = extract_domain(final_url)
    if host and host in FREE_EMAIL_DOMAINS:
        reasons.append("webmail_redirect")
        return 0, reasons, "rejected_webmail"

    if host and any(social in host for social in SOCIAL_REDIRECT_HOSTS):
        reasons.append("redirect_social")
        return max(0, score - 40), reasons, "redirect_social"

    for kw in INCOMPATIBLE_TITLE_KEYWORDS:
        if kw in title_norm:
            reasons.append(f"title_incompativel:{kw}")
            score -= 30

    if fantasia_norm and len(fantasia_norm) >= 4 and fantasia_norm in content:
        score += 30
        reasons.append("nome_fantasia_no_conteudo")
    elif razao_norm and len(razao_norm) >= 6 and razao_norm in content:
        score += 20
        reasons.append("razao_social_no_conteudo")

    if municipio_norm and municipio_norm in content:
        score += 10
        reasons.append("municipio_no_conteudo")
    if uf and re.search(rf"\b{re.escape(uf.lower())}\b", content):
        score += 10
        reasons.append("uf_no_conteudo")

    if telefone:
        tel_digits = re.sub(r"\D", "", telefone)[-10:]
        if tel_digits and tel_digits in re.sub(r"\D", "", content):
            score += 15
            reasons.append("telefone_coincide")

    if cnpj:
        cnpj_digits = re.sub(r"\D", "", cnpj)
        if cnpj_digits in re.sub(r"\D", "", content):
            score += 20
            reasons.append("cnpj_no_site")

    if email_tipo == "corporativo" and email_domain and host and email_domain in host:
        score += 15
        reasons.append("dominio_email_coincide")

    if domain_shared_count >= 3:
        score -= 30
        reasons.append("dominio_compartilhado")
    elif domain_shared_count >= 1:
        score -= 10
        reasons.append("dominio_possivelmente_compartilhado")

    score = max(0, min(100, score))
    if score >= 70:
        status = "validated"
    elif score >= 40:
        status = "partial_match"
    else:
        status = "unverified"
    return score, reasons, status


def http_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    return session
