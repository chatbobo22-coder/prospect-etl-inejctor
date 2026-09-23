"""Validação passiva de e-mail, sem tentativa de entrega SMTP."""

from __future__ import annotations

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

    resolver = dns.resolver.Resolver(configure=True)
    resolver.timeout = min(timeout, 5.0)
    resolver.lifetime = timeout
    mx_hosts: list[str] = []
    mx_valid: bool | None = None
    error: str | None = None
    try:
        answers = resolver.resolve(domain, "MX")
        mx_hosts = sorted({str(answer.exchange).rstrip(".").lower() for answer in answers})
        mx_valid = bool(mx_hosts)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        mx_valid = False
        reasons.append("no_mx")
    except (dns.resolver.NoNameservers, dns.exception.Timeout) as exc:
        mx_valid = None
        error = exc.__class__.__name__
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
