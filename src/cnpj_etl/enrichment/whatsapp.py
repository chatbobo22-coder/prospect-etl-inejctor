"""Extração e validação de WhatsApp."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

VALID_BRAZIL_DDD = frozenset(
    {
        "11",
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "18",
        "19",
        "21",
        "22",
        "24",
        "27",
        "28",
        "31",
        "32",
        "33",
        "34",
        "35",
        "37",
        "38",
        "41",
        "42",
        "43",
        "44",
        "45",
        "46",
        "47",
        "48",
        "49",
        "51",
        "53",
        "54",
        "55",
        "61",
        "62",
        "63",
        "64",
        "65",
        "66",
        "67",
        "68",
        "69",
        "71",
        "73",
        "74",
        "75",
        "77",
        "79",
        "81",
        "82",
        "83",
        "84",
        "85",
        "86",
        "87",
        "88",
        "89",
        "91",
        "92",
        "93",
        "94",
        "95",
        "96",
        "97",
        "98",
        "99",
    }
)


def normalize_brazil_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) in (10, 11) and not digits.startswith("55"):
        digits = "55" + digits
    if len(digits) not in (12, 13) or not digits.startswith("55"):
        return None
    ddd = digits[2:4]
    if ddd not in VALID_BRAZIL_DDD:
        return None
    local = digits[4:]
    if len(local) < 8 or len(set(local)) == 1:
        return None
    return digits


def extract_whatsapp_candidate(href: str, base_url: str | None = None) -> dict:
    """Extrai candidato WhatsApp de um href. Preserva query string para phone=."""
    result = {
        "detected": False,
        "number": None,
        "normalized": None,
        "valid": False,
        "generic_link": False,
        "canonical_url": None,
        "confidence": 0,
    }
    if not href:
        return result
    if base_url and href.startswith("/"):
        href = urljoin(base_url, href)
    lower = href.lower().strip()
    if not any(
        token in lower
        for token in ("wa.me/", "api.whatsapp.com/", "web.whatsapp.com/", "whatsapp://")
    ):
        return result

    result["detected"] = True
    parsed = urlparse(href)
    number: str | None = None

    if "wa.me" in parsed.netloc.lower():
        path_digits = re.sub(r"\D", "", parsed.path.strip("/"))
        if path_digits:
            number = path_digits
    elif parsed.scheme == "whatsapp":
        qs = parse_qs(parsed.query)
        phone_vals = qs.get("phone") or qs.get("Phone") or []
        if phone_vals:
            number = unquote(phone_vals[0])
    else:
        qs = parse_qs(parsed.query)
        phone_vals = qs.get("phone") or qs.get("Phone") or []
        if phone_vals:
            number = unquote(phone_vals[0])

    if number:
        normalized = normalize_brazil_phone(number)
        if normalized:
            result["number"] = number
            result["normalized"] = normalized
            result["valid"] = True
            result["canonical_url"] = f"https://wa.me/{normalized}"
            result["confidence"] = 90
            return result

    result["generic_link"] = True
    result["confidence"] = 10
    return result


def phone_to_whatsapp_candidate(telefone: str | None) -> str | None:
    """Telefone CNPJ vira apenas candidato — nunca WhatsApp confirmado."""
    normalized = normalize_brazil_phone(telefone)
    if not normalized:
        return None
    return f"https://wa.me/{normalized}"


def extract_whatsapp_from_html(html: str, base_url: str | None) -> dict:
    best: dict | None = None
    for match in re.finditer(r"""href=["']([^"']+)["']""", html, re.I):
        candidate = extract_whatsapp_candidate(match.group(1), base_url)
        if not candidate["detected"]:
            continue
        if candidate["valid"]:
            return candidate
        if best is None:
            best = candidate
    return best or {
        "detected": False,
        "number": None,
        "normalized": None,
        "valid": False,
        "generic_link": False,
        "canonical_url": None,
        "confidence": 0,
    }
