"""Coletores de dados empresariais públicos e profissionais."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import csv
import io
import json
import os
import re
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup
import requests

from ..enrichment.models import EnrichSettings
from ..enrichment.website import http_session, safe_fetch
from .models import IntelligenceSettings, Person, Signal, SourceResult

ADMIN_QUALIFICATIONS = {"05", "10", "16", "17", "49"}
DECISION_WORDS = re.compile(
    r"\b(ceo|cio|cto|coo|cfo|diretor|diretora|gerente|gestor|gestora|head|fundador|fundadora|s[oó]cio|s[oó]cia|propriet[aá]rio|propriet[aá]ria)\b",
    re.I,
)
INTENT_WORDS = {
    "expansão": 10,
    "expansao": 10,
    "investimento": 9,
    "nova unidade": 9,
    "inaugura": 8,
    "contrata": 7,
    "vagas": 6,
    "lançamento": 5,
    "lancamento": 5,
    "crescimento": 5,
}

_CVM_CACHE: dict[str, dict] | None = None


def collect_receita(conn, company: dict, _: IntelligenceSettings) -> SourceResult:
    cnpj_basico = company["cnpj_basico"]
    rows = conn.execute(
        """
        SELECT s.nome_socio_razao_social, s.qualificacao_socio, q.descricao,
               s.data_entrada_sociedade
        FROM cnpj.socios s
        LEFT JOIN cnpj.qualificacoes_socios q ON q.codigo = s.qualificacao_socio
        WHERE s.cnpj_basico = %s AND s.nome_socio_razao_social IS NOT NULL
        ORDER BY s.data_entrada_sociedade NULLS LAST
        """,
        (cnpj_basico,),
    ).fetchall()
    people = []
    for name, code, role, joined_at in rows:
        admin = (code or "").strip() in ADMIN_QUALIFICATIONS
        people.append(
            Person(
                full_name=name.strip(),
                role_title=role or "Sócio",
                relationship_type="administrator" if admin else "partner",
                is_decision_maker=admin,
                confidence=95,
                raw_data={
                    "qualification_code": (code or "").strip() or None,
                    "joined_at": joined_at.isoformat() if joined_at else None,
                },
            )
        )

    signals = [
        Signal("active_registry", "fit", "Empresa ativa na Receita Federal", 6, 100),
        Signal("official_identity", "confidence", "Identidade empresarial confirmada", 5, 100),
    ]
    capital = float(company.get("capital_social") or 0)
    if capital >= 1_000_000:
        signals.append(Signal("capital_social", "capacity", "Capital social acima de R$ 1 milhão", 10, 100))
    elif capital >= 100_000:
        signals.append(Signal("capital_social", "capacity", "Capital social acima de R$ 100 mil", 6, 100))
    elif capital > 0:
        signals.append(Signal("capital_social", "capacity", "Capital social declarado", 2, 100))
    branches = conn.execute(
        "SELECT COUNT(*) FROM cnpj.estabelecimentos WHERE cnpj_basico=%s AND situacao_cadastral='02'",
        (cnpj_basico,),
    ).fetchone()[0]
    if branches > 1:
        signals.append(
            Signal(
                "active_branches",
                "capacity",
                f"{branches} estabelecimentos ativos",
                min(8, 2 + int(branches)),
                100,
                raw_data={"active_branches": branches},
            )
        )
    return SourceResult("receita", people=people, signals=signals, metadata={"partners": len(people)})


def collect_website(conn, company: dict, settings: IntelligenceSettings) -> SourceResult:
    url = company.get("site_final_url") or company.get("site_url")
    if not url or not company.get("site_valid"):
        return SourceResult("website", status="no_data", metadata={"reason": "no_valid_site"})
    enrich_settings = EnrichSettings(
        request_timeout=settings.request_timeout,
        crawler_max_pages=settings.website_max_pages,
    )
    session = http_session()
    queue = [url]
    seen: set[str] = set()
    people: list[Person] = []
    signals: list[Signal] = [Signal("valid_website", "presence", "Site institucional validado", 3, 95, source_url=url)]
    digital_lead_score = int(company.get("digital_lead_score") or 0)
    if digital_lead_score >= 60:
        signals.append(
            Signal(
                "digital_fit",
                "fit",
                f"Aderência digital previamente validada ({digital_lead_score}/100)",
                18 if digital_lead_score >= 75 else 14,
                int(company.get("digital_confidence_score") or 75),
                source_url=url,
            )
        )
    if not company.get("has_chat"):
        signals.append(Signal("no_chat", "pain", "Site sem atendimento por chat", 5, 85, source_url=url))
    if not company.get("has_contact_form"):
        signals.append(Signal("no_contact_form", "pain", "Site sem formulário de contato detectado", 4, 80, source_url=url))
    if company.get("commerce_maturity") in {"catalogo_sem_checkout", "sem_ecommerce"}:
        signals.append(
            Signal(
                "commerce_gap",
                "pain",
                "Oportunidade de evolução na jornada comercial digital",
                8,
                80,
                source_url=url,
            )
        )
    has_careers = False
    while queue and len(seen) < settings.website_max_pages:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        fetched = safe_fetch(session, current, enrich_settings)
        if not fetched.reachable or not fetched.html:
            continue
        page_url = fetched.final_url or current
        soup = BeautifulSoup(fetched.html, "html.parser")
        people.extend(_jsonld_people(soup, page_url))
        for anchor in soup.select("a[href]"):
            href = urljoin(page_url, anchor.get("href", ""))
            label = f"{anchor.get_text(' ', strip=True)} {href}".lower()
            if any(word in label for word in ("carreira", "trabalhe-conosco", "vagas", "jobs")):
                has_careers = True
            if any(word in label for word in ("equipe", "time", "team", "diretoria", "leadership", "quem-somos", "sobre")):
                if _same_domain(url, href) and href not in seen and href not in queue:
                    queue.append(href)
    if has_careers:
        signals.append(
            Signal(
                "careers_page",
                "intent",
                "Página pública de carreiras ou vagas ativa",
                6,
                75,
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                source_url=url,
            )
        )
    people = _deduplicate_people(people)
    return SourceResult(
        "website",
        people=people,
        signals=signals,
        metadata={"pages_checked": len(seen), "public_people": len(people)},
    )


def collect_rdap(_: object, company: dict, settings: IntelligenceSettings) -> SourceResult:
    domain = (company.get("email_dominio") or "").lower().strip()
    if not domain:
        site = company.get("site_final_url") or company.get("site_url") or ""
        domain = (urlparse(site).hostname or "").lower().removeprefix("www.")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        return SourceResult("rdap", status="no_data", metadata={"reason": "no_domain"})
    response = requests.get(f"https://rdap.org/domain/{quote(domain, safe='.')}", timeout=settings.request_timeout)
    if response.status_code == 404:
        return SourceResult("rdap", status="no_data", metadata={"domain": domain})
    response.raise_for_status()
    payload = response.json()
    events = {e.get("eventAction"): e.get("eventDate") for e in payload.get("events", [])}
    registered = _parse_date(events.get("registration"))
    signals = [Signal("domain_registered", "confidence", "Domínio empresarial registrado", 3, 85)]
    if registered:
        age_years = (datetime.now(timezone.utc) - registered).days / 365.25
        signals.append(
            Signal(
                "domain_age",
                "capacity",
                f"Domínio com {age_years:.1f} anos",
                5 if age_years >= 5 else 3 if age_years >= 2 else 1,
                80,
                raw_data={"registered_at": registered.isoformat(), "age_years": round(age_years, 1)},
            )
        )
    return SourceResult("rdap", signals=signals, metadata={"domain": domain, "events": events})


def collect_gdelt(_: object, company: dict, settings: IntelligenceSettings) -> SourceResult:
    name = company.get("nome_fantasia") or company.get("razao_social")
    if not name or len(name.strip()) < 4:
        return SourceResult("gdelt", status="no_data")
    query = f'"{name.strip()}"'
    response = requests.get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": settings.gdelt_max_records,
            "timespan": "3months",
        },
        timeout=settings.request_timeout,
    )
    response.raise_for_status()
    articles = response.json().get("articles", [])
    signals: list[Signal] = []
    for article in articles:
        title = article.get("title") or ""
        lower = title.lower()
        matched = [(word, score) for word, score in INTENT_WORDS.items() if word in lower]
        if not matched:
            continue
        word, score = max(matched, key=lambda item: item[1])
        seen = _parse_gdelt_date(article.get("seendate"))
        signals.append(
            Signal(
                f"news_{word.replace(' ', '_')}",
                "intent",
                title[:250],
                score,
                65,
                observed_at=seen,
                expires_at=(seen or datetime.now(timezone.utc)) + timedelta(days=90),
                source_url=article.get("url"),
                raw_data={"domain": article.get("domain"), "keyword": word},
            )
        )
    return SourceResult(
        "gdelt",
        signals=signals,
        status="success" if signals else "no_data",
        metadata={"articles_found": len(articles), "relevant_signals": len(signals)},
    )


def collect_google_places(_: object, company: dict, __: IntelligenceSettings) -> SourceResult:
    if not company.get("google_places_checked_at"):
        return SourceResult("google_places", status="skipped", metadata={"reason": "not_checked"})
    if not company.get("google_place_id"):
        return SourceResult("google_places", status="no_data")
    signals = [Signal("business_listing", "confidence", "Operação confirmada no Google Business", 4, 90)]
    rating_count = int(company.get("google_rating_count") or 0)
    rating = float(company.get("google_rating") or 0)
    if rating_count >= 20:
        signals.append(
            Signal(
                "customer_reviews",
                "presence",
                f"{rating_count} avaliações públicas, nota {rating:.1f}",
                4,
                90,
                source_url=company.get("google_maps_url"),
            )
        )
    return SourceResult("google_places", signals=signals, metadata={"place_id": company["google_place_id"]})


def collect_pagespeed(_: object, company: dict, settings: IntelligenceSettings) -> SourceResult:
    url = company.get("site_final_url") or company.get("site_url")
    if not settings.pagespeed_api_key:
        return SourceResult("pagespeed", status="skipped", metadata={"reason": "missing_api_key"})
    if not url:
        return SourceResult("pagespeed", status="no_data")
    response = requests.get(
        "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
        params={"url": url, "strategy": "mobile", "category": "performance", "key": settings.pagespeed_api_key},
        timeout=max(30, settings.request_timeout),
    )
    response.raise_for_status()
    score = round(float(response.json().get("lighthouseResult", {}).get("categories", {}).get("performance", {}).get("score", 0)) * 100)
    signals = []
    if score < 50:
        signals.append(Signal("slow_site", "pain", f"Site móvel lento ({score}/100)", 10, 95, source_url=url))
    elif score < 75:
        signals.append(Signal("site_performance", "pain", f"Desempenho móvel pode melhorar ({score}/100)", 5, 95, source_url=url))
    return SourceResult("pagespeed", signals=signals, metadata={"performance_score": score})


def collect_cvm(_: object, company: dict, settings: IntelligenceSettings) -> SourceResult:
    global _CVM_CACHE
    if _CVM_CACHE is None:
        response = requests.get(
            "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv",
            timeout=max(30, settings.request_timeout),
        )
        response.raise_for_status()
        text = response.content.decode("latin-1")
        _CVM_CACHE = {}
        for row in csv.DictReader(io.StringIO(text), delimiter=";"):
            cnpj = re.sub(r"\D", "", row.get("CNPJ_CIA", ""))
            if cnpj:
                _CVM_CACHE[cnpj] = row
    record = _CVM_CACHE.get(company["cnpj"])
    if not record:
        return SourceResult("cvm", status="no_data")
    status = record.get("SIT") or record.get("SIT_REG") or "registrada"
    signal = Signal(
        "public_company",
        "capacity",
        f"Companhia registrada na CVM ({status})",
        12,
        100,
        source_url="https://dados.cvm.gov.br/dataset/cia_aberta-cad",
        raw_data={
            "cvm_code": record.get("CD_CVM"),
            "registration_status": status,
            "sector": record.get("SETOR_ATIV"),
        },
    )
    return SourceResult("cvm", signals=[signal], metadata={"cvm_code": record.get("CD_CVM")})


def collect_configured_provider(_: object, company: dict, settings: IntelligenceSettings, source_code: str) -> SourceResult:
    template = os.getenv(f"{source_code.upper()}_LOOKUP_URL_TEMPLATE", "").strip()
    if not template:
        return SourceResult(source_code, status="skipped", metadata={"reason": "provider_not_configured"})
    url = template.format(cnpj=company["cnpj"], name=quote(company.get("razao_social") or ""))
    headers = {"Accept": "application/json"}
    if settings.provider_api_key:
        headers["Authorization"] = f"Bearer {settings.provider_api_key}"
    response = requests.get(url, headers=headers, timeout=settings.request_timeout)
    response.raise_for_status()
    payload = response.json()
    people = [_provider_person(item) for item in payload.get("people", []) if item.get("full_name")]
    signals = [_provider_signal(item) for item in payload.get("signals", []) if item.get("title")]
    return SourceResult(
        source_code,
        people=people,
        signals=signals,
        status="success" if people or signals else "no_data",
        metadata={"records": len(people) + len(signals)},
    )


COLLECTORS = {
    "receita": collect_receita,
    "website": collect_website,
    "rdap": collect_rdap,
    "gdelt": collect_gdelt,
    "google_places": collect_google_places,
    "pagespeed": collect_pagespeed,
    "cvm": collect_cvm,
}


def collect_source(source_code: str, conn, company: dict, settings: IntelligenceSettings) -> SourceResult:
    collector = COLLECTORS.get(source_code)
    if collector:
        return collector(conn, company, settings)
    if source_code in {"pncp", "inpi", "meta_ads", "google_ads", "people_provider"}:
        return collect_configured_provider(conn, company, settings, source_code)
    raise ValueError(f"Fonte de inteligência desconhecida: {source_code}")


def _jsonld_people(soup: BeautifulSoup, source_url: str) -> list[Person]:
    found: list[Person] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _walk_json(payload):
            types = item.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if "Person" not in types or not item.get("name"):
                continue
            role = item.get("jobTitle") or item.get("roleName")
            same_as = item.get("sameAs") or []
            if isinstance(same_as, str):
                same_as = [same_as]
            linkedin = next((u for u in same_as if "linkedin.com/in/" in str(u).lower()), None)
            decision = bool(role and DECISION_WORDS.search(str(role)))
            relationship = "executive" if decision else "employee"
            if role and re.search(r"fundador|fundadora|founder", str(role), re.I):
                relationship = "founder"
            found.append(
                Person(
                    full_name=str(item["name"]).strip(),
                    role_title=str(role).strip() if role else None,
                    relationship_type=relationship,
                    linkedin_url=linkedin,
                    business_email=item.get("email"),
                    business_phone=item.get("telephone"),
                    is_decision_maker=decision,
                    confidence=85,
                    source_url=source_url,
                )
            )
    return found


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _deduplicate_people(people: list[Person]) -> list[Person]:
    unique: dict[tuple[str, str], Person] = {}
    for person in people:
        key = (person.full_name.casefold(), (person.role_title or "").casefold())
        unique[key] = person
    return list(unique.values())


def _same_domain(base: str, candidate: str) -> bool:
    return (urlparse(base).hostname or "").removeprefix("www.") == (urlparse(candidate).hostname or "").removeprefix("www.")


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_gdelt_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _provider_person(item: dict) -> Person:
    return Person(
        full_name=item["full_name"],
        role_title=item.get("role_title"),
        relationship_type=item.get("relationship_type", "employee"),
        linkedin_url=item.get("linkedin_url"),
        business_email=item.get("business_email"),
        business_phone=item.get("business_phone"),
        is_decision_maker=bool(item.get("is_decision_maker")),
        confidence=max(0, min(100, int(item.get("confidence", 60)))),
        source_url=item.get("source_url"),
        raw_data=item.get("raw_data") or {},
    )


def _provider_signal(item: dict) -> Signal:
    category = item.get("category", "intent")
    if category not in {"fit", "capacity", "intent", "pain", "confidence", "presence", "risk"}:
        category = "intent"
    return Signal(
        signal_type=item.get("signal_type", "provider_signal"),
        category=category,
        title=item["title"],
        description=item.get("description"),
        score=max(-100, min(100, int(item.get("score", 0)))),
        confidence=max(0, min(100, int(item.get("confidence", 60)))),
        source_url=item.get("source_url"),
        raw_data=item.get("raw_data") or {},
    )
