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

ACCOUNTING_EMAIL_PREFIXES = frozenset(
    {"fiscal", "financeiro", "contabilidade", "contador", "nfe", "faturamento", "dp", "rh"}
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
    if local in ACCOUNTING_EMAIL_PREFIXES or any(
        local.startswith(p) for p in ACCOUNTING_EMAIL_PREFIXES
    ):
        return "accounting"
    if local in {"financeiro", "fiscal", "nfe"}:
        return "finance"
    if local in {"contato", "atendimento", "vendas", "comercial", "sales", "sac"}:
        return "sales"
    if local in {"suporte", "support", "help"}:
        return "support"
    if re.match(r"^[a-z]+\.[a-z]+$", local):
        return "personal"
    return "general"


def is_free_email_domain(domain: str | None) -> bool:
    return bool(domain and domain.lower() in FREE_EMAIL_DOMAINS)
