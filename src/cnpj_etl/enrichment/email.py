"""Classificação de e-mail corporativo vs gratuito."""

from __future__ import annotations

import re

FREE_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "outlook.com",
        "outlook.com.br",
        "live.com",
        "yahoo.com",
        "yahoo.com.br",
        "yahoo.com.ar",
        "uol.com.br",
        "terra.com.br",
        "bol.com.br",
        "icloud.com",
        "proton.me",
        "protonmail.com",
        "ig.com.br",
        "msn.com",
        "ymail.com",
    }
)

BLOCKED_OUTREACH_EMAIL_PREFIXES = frozenset(
    {
        "administrativo",
        "boleto",
        "boletos",
        "cobranca",
        "contabilidade",
        "contador",
        "departamentopessoal",
        "dp",
        "faturamento",
        "financeiro",
        "fiscal",
        "nfe",
        "pagamento",
        "pagamentos",
        "rh",
        "tributario",
    }
)


def classify_email(email: str | None) -> tuple[str | None, str | None, str]:
    if not email or "@" not in email:
        return None, None, "invalido"
    email = email.strip().lower()
    domain = email.rsplit("@", 1)[1]
    if domain in FREE_EMAIL_DOMAINS:
        return domain, email, "gratuito"
    return domain, email, "corporativo"


def email_local_part(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    return email.split("@", 1)[0].lower()


def classify_email_role(email: str | None) -> str:
    local = email_local_part(email)
    if not local:
        return "unknown"
    if local in BLOCKED_OUTREACH_EMAIL_PREFIXES or any(
        local.startswith(p) for p in BLOCKED_OUTREACH_EMAIL_PREFIXES
    ):
        return "blocked_backoffice"
    if local in {"contato", "atendimento", "vendas", "comercial", "sales", "sac"}:
        return "sales"
    if local in {"suporte", "support", "help"}:
        return "support"
    if re.match(r"^[a-z]+\.[a-z]+$", local):
        return "personal"
    return "general"


def is_valid_email(email: str | None) -> bool:
    if not email:
        return False
    value = email.strip().lower()
    if len(value) > 254 or value.count("@") != 1:
        return False
    local, domain = value.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        return False
    if local.startswith(".") or local.endswith(".") or ".." in value:
        return False
    return bool(re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)) and bool(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", domain)
    )


def is_blocked_outreach_email(email: str | None) -> bool:
    return classify_email_role(email) == "blocked_backoffice"


def is_free_email_domain(domain: str | None) -> bool:
    return bool(domain and domain.lower() in FREE_EMAIL_DOMAINS)
