"""Validação passiva de e-mail, sem tentativa de entrega SMTP."""

from __future__ import annotations

from functools import lru_cache

import dns.exception
import dns.resolver

from ..enrichment.email import classify_email, classify_email_role, is_valid_email

DISPOSABLE_DOMAINS = frozenset(
    {
        "10minutemail.com",
        "dispostable.com",
        "emailondeck.com",
        "guerrillamail.com",
        "maildrop.cc",
        "mailinator.com",
        "sharklasers.com",
        "temp-mail.org",
        "tempmail.com",
        "throwawaymail.com",
        "yopmail.com",
    }
)


@lru_cache(maxsize=20_000)
def _resolve_mx(domain: str, timeout_seconds: int) -> tuple[bool | None, tuple[str, ...], str | None]:
    """Resolve MX uma vez por domínio durante a execução do worker."""
    resolver = dns.resolver.Resolver(configure=True)
    resolver.timeout = min(float(timeout_seconds), 5.0)
    resolver.lifetime = float(timeout_seconds)
    try:
        answers = resolver.resolve(domain, "MX")
        hosts = tuple(sorted({str(answer.exchange).rstrip(".").lower() for answer in answers}))
        return bool(hosts), hosts, None
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return False, (), None
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        return None, (), exc.__class__.__name__


def clear_mx_cache() -> None:
    _resolve_mx.cache_clear()


def verify_email(email: str | None, timeout: float = 5.0) -> dict:
    domain, normalized, email_type = classify_email(email)
    role = classify_email_role(normalized)
    reasons: list[str] = []
    if not is_valid_email(normalized):
        return {
            "email": (email or "").strip().lower(),
            "domain": domain,
            "syntax_valid": False,
            "mx_valid": False,
            "mx_hosts": [],
            "disposable": False,
            "email_role": role,
            "email_type": email_type,
            "deliverability_status": "invalid",
            "risk_score": 100,
            "reason_codes": ["invalid_syntax"],
            "error": None,
        }

    disposable = domain in DISPOSABLE_DOMAINS
    if disposable:
        reasons.append("disposable_domain")
    if role == "blocked_backoffice":
        reasons.append("blocked_backoffice_role")

    timeout_seconds = max(1, min(30, round(timeout)))
    mx_valid, cached_hosts, error = _resolve_mx(domain, timeout_seconds)
    mx_hosts = list(cached_hosts)
    if mx_valid is False:
        reasons.append("no_mx")
    elif mx_valid is None:
        reasons.append("mx_check_unavailable")

    if disposable or mx_valid is False or role == "blocked_backoffice":
        status = "invalid"
    elif mx_valid is None:
        status = "unknown"
    elif role in {"general", "support"} and email_type != "gratuito":
        status = "risky"
        reasons.append("generic_mailbox")
    else:
        status = "valid"

    risk = 0
    risk += 100 if disposable or mx_valid is False else 25 if mx_valid is None else 0
    risk += (
        100
        if role == "blocked_backoffice"
        else 20
        if role in {"general", "support"} and email_type != "gratuito"
        else 0
    )
    return {
        "email": normalized,
        "domain": domain,
        "syntax_valid": True,
        "mx_valid": mx_valid,
        "mx_hosts": mx_hosts,
        "disposable": disposable,
        "email_role": role,
        "email_type": email_type,
        "deliverability_status": status,
        "risk_score": min(100, risk),
        "reason_codes": list(dict.fromkeys(reasons)),
        "error": error,
    }
