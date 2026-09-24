"""Coletores de dados empresariais públicos e profissionais."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import csv
import io
import json
import logging
import os
import re
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup
import requests

from ..enrichment.models import EnrichSettings
from ..enrichment.website import http_session, safe_fetch
from .models import IntelligenceSettings, Person, Signal, SourceResult
from .email_quality import verify_email

log = logging.getLogger(__name__)

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
        signals.append(
            Signal("capital_social", "capacity", "Capital social acima de R$ 1 milhão", 10, 100)
        )
    elif capital >= 100_000:
        signals.append(
            Signal("capital_social", "capacity", "Capital social acima de R$ 100 mil", 6, 100)
        )
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
    return SourceResult(
        "receita", people=people, signals=signals, metadata={"partners": len(people)}
    )


def collect_email_quality(_: object, company: dict, settings: IntelligenceSettings) -> SourceResult:
    verification = verify_email(company.get("email"), timeout=min(settings.request_timeout, 8))
    status = verification["deliverability_status"]
    if status == "invalid":
        signal = Signal(
            "invalid_email",
            "risk",
            "E-mail inadequado para outreach",
            25,
            100,
            description=", ".join(verification["reason_codes"]),
            raw_data={"status": status, "risk_score": verification["risk_score"]},
        )
    elif status == "valid":
        signal = Signal(
            "deliverable_email",
            "confidence",
            "Domínio de e-mail com entrega tecnicamente válida",
            4,
            95,
            raw_data={"status": status, "email_type": verification["email_type"]},
        )
    else:
        signal = Signal(
            "email_risk",
            "risk",
            "E-mail requer cautela",
            8,
            80,
            description=", ".join(verification["reason_codes"]),
            raw_data={"status": status, "risk_score": verification["risk_score"]},
        )
    return SourceResult("email_quality", signals=[signal], metadata={"verification": verification})


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
    signals: list[Signal] = [
        Signal("valid_website", "presence", "Site institucional validado", 3, 95, source_url=url)
    ]
    technologies = []
    for platform in company.get("plataformas_detectadas") or []:
        technologies.append(
            {"name": platform, "category": "commerce_platform", "confidence": 85, "source_url": url}
        )
    if company.get("chat_provider"):
        technologies.append(
            {
                "name": company["chat_provider"],
                "category": "customer_service",
                "confidence": 85,
                "source_url": url,
            }
        )
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
        signals.append(
            Signal("no_chat", "pain", "Site sem atendimento por chat", 5, 85, source_url=url)
        )
    if not company.get("has_contact_form"):
        signals.append(
            Signal(
                "no_contact_form",
                "pain",
                "Site sem formulário de contato detectado",
                4,
                80,
                source_url=url,
            )
        )
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
            if any(
                word in label
                for word in (
                    "equipe",
                    "time",
                    "team",
                    "diretoria",
                    "leadership",
                    "quem-somos",
                    "sobre",
                )
            ):
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
        metadata={
            "pages_checked": len(seen),
            "public_people": len(people),
            "technologies": technologies,
        },
    )


def collect_rdap(_: object, company: dict, settings: IntelligenceSettings) -> SourceResult:
    domain = (company.get("email_dominio") or "").lower().strip()
    if not domain:
        site = company.get("site_final_url") or company.get("site_url") or ""
        domain = (urlparse(site).hostname or "").lower().removeprefix("www.")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        return SourceResult("rdap", status="no_data", metadata={"reason": "no_domain"})
    response = requests.get(
        f"https://rdap.org/domain/{quote(domain, safe='.')}", timeout=settings.request_timeout
    )
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
                raw_data={
                    "registered_at": registered.isoformat(),
                    "age_years": round(age_years, 1),
                },
            )
        )
    return SourceResult("rdap", signals=signals, metadata={"domain": domain, "events": events})


def collect_gdelt(_: object, company: dict, settings: IntelligenceSettings) -> SourceResult:
    name = company.get("nome_fantasia") or company.get("razao_social")
    if not name or len(name.strip()) < 4:
        return SourceResult("gdelt", status="no_data")
    query = f'"{name.strip()}"'
    try:
        response = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": query,
                "mode": "ArtList",
                "format": "json",
                "maxrecords": settings.gdelt_max_records,
                "timespan": "3months",
            },
            headers={
                "Accept": "application/json",
                "User-Agent": "MestreLead/1.0 (public-company-intelligence)",
            },
            timeout=max(settings.request_timeout, settings.gdelt_timeout_seconds),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        reason = "rate_limited" if status_code == 429 else "temporarily_unavailable"
        log.warning("GDELT indisponível para %s: %s", company.get("cnpj"), exc)
        return SourceResult(
            "gdelt",
            status="skipped",
            metadata={
                "reason": reason,
                "status_code": status_code,
                "error_type": type(exc).__name__,
            },
        )
    articles = payload.get("articles", []) if isinstance(payload, dict) else []
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
    signals = [
        Signal("business_listing", "confidence", "Operação confirmada no Google Business", 4, 90)
    ]
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
    return SourceResult(
        "google_places", signals=signals, metadata={"place_id": company["google_place_id"]}
    )


def collect_pagespeed(_: object, company: dict, settings: IntelligenceSettings) -> SourceResult:
    url = company.get("site_final_url") or company.get("site_url")
    if not settings.pagespeed_api_key:
        return SourceResult("pagespeed", status="skipped", metadata={"reason": "missing_api_key"})
    if not url:
        return SourceResult("pagespeed", status="no_data")
    response = requests.get(
        "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
        params={
            "url": url,
            "strategy": "mobile",
            "category": "performance",
            "key": settings.pagespeed_api_key,
        },
        timeout=max(30, settings.request_timeout),
    )
    response.raise_for_status()
    score = round(
        float(
            response.json()
            .get("lighthouseResult", {})
            .get("categories", {})
            .get("performance", {})
            .get("score", 0)
        )
        * 100
    )
    signals = []
    if score < 50:
        signals.append(
            Signal("slow_site", "pain", f"Site móvel lento ({score}/100)", 10, 95, source_url=url)
        )
    elif score < 75:
        signals.append(
            Signal(
                "site_performance",
                "pain",
                f"Desempenho móvel pode melhorar ({score}/100)",
                5,
                95,
                source_url=url,
            )
        )
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


def collect_apollo(_: object, company: dict, settings: IntelligenceSettings) -> SourceResult:
    """Busca decisores e sinais empresariais pela API oficial da Apollo."""
    if not settings.apollo_api_key:
        return SourceResult("apollo", status="skipped", metadata={"reason": "missing_api_key"})
    domain = _company_domain(company)
    if not domain:
        return SourceResult("apollo", status="no_data", metadata={"reason": "no_domain"})
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "x-api-key": settings.apollo_api_key,
    }
    response = requests.post(
        "https://api.apollo.io/api/v1/mixed_people/api_search",
        headers=headers,
        params=[
            ("q_organization_domains_list[]", domain),
            *(("person_seniorities[]", value) for value in (
                "owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager"
            )),
            ("page", 1),
            ("per_page", max(1, min(settings.provider_people_limit, 10))),
        ],
        timeout=settings.request_timeout,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("people") or []
    people: list[Person] = []
    company_profile: dict = {}
    for item in rows[: settings.provider_people_limit]:
        enriched = item
        if settings.reveal_provider_emails:
            params = {
                "name": item.get("name") or _join_name(item),
                "domain": domain,
                "reveal_personal_emails": "false",
                "reveal_phone_number": "false",
            }
            detail = requests.post(
                "https://api.apollo.io/api/v1/people/match",
                headers=headers,
                params={key: value for key, value in params.items() if value},
                timeout=settings.request_timeout,
            )
            if detail.status_code == 200:
                enriched = detail.json().get("person") or item
            elif detail.status_code not in {400, 404, 422}:
                detail.raise_for_status()
        person = _apollo_person(enriched)
        if person:
            people.append(person)
        company_profile = company_profile or enriched.get("organization") or item.get("organization") or {}
    signals = _professional_company_signals(company_profile, "apollo")
    return SourceResult(
        "apollo",
        people=_deduplicate_people(people),
        signals=signals,
        status="success" if people or signals else "no_data",
        metadata={
            "domain": domain,
            "people_found": len(people),
            "company": _public_company_metadata(company_profile),
            "contact_reveal_enabled": settings.reveal_provider_emails,
            "phone_reveal_enabled": False,
        },
    )


def collect_prospeo(conn, company: dict, settings: IntelligenceSettings) -> SourceResult:
    """Enriquece empresa e pessoas conhecidas pela API oficial da Prospeo."""
    if not settings.prospeo_api_key:
        return SourceResult("prospeo", status="skipped", metadata={"reason": "missing_api_key"})
    domain = _company_domain(company)
    company_linkedin = company.get("linkedin_url")
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "X-KEY": settings.prospeo_api_key,
    }
    company_data = {
        "company_website": domain,
        "company_linkedin_url": company_linkedin,
        "company_name": company.get("nome_fantasia") or company.get("razao_social"),
    }
    company_data = {key: value for key, value in company_data.items() if value}
    company_profile: dict = {}
    if company_data:
        response = requests.post(
            "https://api.prospeo.io/enrich-company",
            headers=headers,
            json={"data": company_data},
            timeout=settings.request_timeout,
        )
        if response.status_code == 200:
            payload = response.json()
            if not payload.get("error"):
                company_profile = payload.get("company") or {}
        elif response.status_code not in {400, 404, 422}:
            response.raise_for_status()

    known_people = _known_people(conn, company["cnpj"], settings.provider_people_limit)
    people: list[Person] = []
    if settings.reveal_provider_emails:
        for known in known_people:
            identity = {
                "full_name": known.get("full_name"),
                "linkedin_url": known.get("linkedin_url"),
                "company_name": company.get("nome_fantasia") or company.get("razao_social"),
                "company_website": domain,
                "company_linkedin_url": company_profile.get("linkedin_url") or company_linkedin,
            }
            identity = {key: value for key, value in identity.items() if value}
            response = requests.post(
                "https://api.prospeo.io/enrich-person",
                headers=headers,
                json={
                    "only_verified_email": True,
                    "enrich_mobile": settings.reveal_provider_phones,
                    "data": identity,
                },
                timeout=settings.request_timeout,
            )
            if response.status_code != 200:
                if response.status_code not in {400, 404, 422}:
                    response.raise_for_status()
                continue
            payload = response.json()
            person = _prospeo_person(payload.get("person") or {}) if not payload.get("error") else None
            if person:
                people.append(person)
                company_profile = company_profile or payload.get("company") or {}
    signals = _professional_company_signals(company_profile, "prospeo")
    return SourceResult(
        "prospeo",
        people=_deduplicate_people(people),
        signals=signals,
        status="success" if people or signals else "no_data",
        metadata={
            "domain": domain,
            "people_considered": len(known_people),
            "people_enriched": len(people),
            "company": _public_company_metadata(company_profile),
            "phone_reveal_enabled": settings.reveal_provider_phones,
        },
    )


def collect_hunter(_: object, company: dict, settings: IntelligenceSettings) -> SourceResult:
    """Obtém contatos profissionais validados por domínio usando Hunter."""
    if not settings.hunter_api_key:
        return SourceResult("hunter", status="skipped", metadata={"reason": "missing_api_key"})
    domain = _company_domain(company)
    if not domain:
        return SourceResult("hunter", status="no_data", metadata={"reason": "no_domain"})
    response = requests.get(
        "https://api.hunter.io/v2/domain-search",
        params={
            "domain": domain,
            "api_key": settings.hunter_api_key,
            "decision_maker": "true",
            "limit": max(1, min(settings.provider_people_limit, 10)),
            "aggregations": "true",
        },
        timeout=settings.request_timeout,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or {}
    people = [
        person
        for item in data.get("emails") or []
        if (person := _hunter_person(item)) is not None
    ]
    aggregations = (payload.get("meta") or {}).get("aggregations") or {}
    company_profile = {
        "name": data.get("organization"),
        "domain": data.get("domain"),
        "linkedin_url": data.get("linkedin"),
        "industry": data.get("industry"),
        "employee_count": data.get("headcount") or data.get("employees"),
        "social_count": sum(bool(data.get(key)) for key in ("linkedin", "twitter", "facebook")),
        "decision_makers": aggregations.get("decision_makers"),
    }
    signals = _professional_company_signals(company_profile, "hunter")
    if people:
        signals.append(
            Signal(
                "validated_professional_contacts",
                "confidence",
                f"{len(people)} contato(s) profissional(is) encontrado(s)",
                min(6, 2 + len(people)),
                90,
                source_url=f"https://hunter.io/search/{domain}",
            )
        )
    return SourceResult(
        "hunter",
        people=_deduplicate_people(people),
        signals=signals,
        status="success" if people or signals else "no_data",
        metadata={
            "domain": domain,
            "people_found": len(people),
            "aggregations": aggregations,
            "company": _public_company_metadata(company_profile),
        },
    )


def collect_configured_provider(
    _: object, company: dict, settings: IntelligenceSettings, source_code: str
) -> SourceResult:
    template = os.getenv(f"{source_code.upper()}_LOOKUP_URL_TEMPLATE", "").strip()
    if not template:
        return SourceResult(
            source_code, status="skipped", metadata={"reason": "provider_not_configured"}
        )
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
    "email_quality": collect_email_quality,
    "website": collect_website,
    "rdap": collect_rdap,
    "gdelt": collect_gdelt,
    "google_places": collect_google_places,
    "pagespeed": collect_pagespeed,
    "cvm": collect_cvm,
    "apollo": collect_apollo,
    "prospeo": collect_prospeo,
    "hunter": collect_hunter,
}


def collect_source(
    source_code: str, conn, company: dict, settings: IntelligenceSettings
) -> SourceResult:
    collector = COLLECTORS.get(source_code)
    if collector:
        return collector(conn, company, settings)
    if source_code in {"pncp", "inpi", "meta_ads", "google_ads", "people_provider"}:
        return collect_configured_provider(conn, company, settings, source_code)
    raise ValueError(f"Fonte de inteligência desconhecida: {source_code}")


def _company_domain(company: dict) -> str | None:
    url = company.get("site_final_url") or company.get("site_url") or ""
    domain = (urlparse(url).hostname or "").lower()
    if not domain and company.get("email_tipo") == "corporativo":
        domain = (company.get("email_dominio") or "").strip().lower()
    domain = domain.removeprefix("www.").strip(".")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}", domain):
        return None
    return domain


def _join_name(item: dict) -> str:
    return " ".join(
        part.strip() for part in (item.get("first_name") or "", item.get("last_name") or "")
        if part.strip()
    )


def _relationship(role: str | None, seniority: str | None = None) -> tuple[str, bool]:
    value = f"{role or ''} {seniority or ''}".strip()
    decision = bool(DECISION_WORDS.search(value)) or (seniority or "").lower() in {
        "owner", "founder", "c_suite", "partner", "vp", "head", "director", "executive"
    }
    if re.search(r"fundador|fundadora|founder|owner", value, re.I):
        return "founder", True
    return ("executive", True) if decision else ("employee", False)


def _apollo_person(item: dict) -> Person | None:
    name = item.get("name") or _join_name(item)
    if not name:
        return None
    role = item.get("title")
    relationship, decision = _relationship(role, item.get("seniority"))
    phones = item.get("phone_numbers") or []
    phone = next(
        (p.get("sanitized_number") or p.get("raw_number") for p in phones if isinstance(p, dict)),
        None,
    )
    return Person(
        full_name=name,
        role_title=role,
        relationship_type=relationship,
        linkedin_url=item.get("linkedin_url"),
        business_email=item.get("email"),
        business_phone=phone,
        is_decision_maker=decision,
        confidence={"high": 95, "medium": 80, "low": 60}.get(item.get("match_confidence"), 80),
        source_url=item.get("linkedin_url"),
        raw_data={
            "provider_id": item.get("id"),
            "email_status": item.get("email_status"),
            "seniority": item.get("seniority"),
            "departments": item.get("departments") or [],
        },
    )


def _prospeo_person(item: dict) -> Person | None:
    name = item.get("full_name") or _join_name(item)
    if not name:
        return None
    current_job = item.get("current_job") or item.get("job") or {}
    role = item.get("job_title") or current_job.get("title")
    seniority = item.get("seniority") or current_job.get("seniority")
    relationship, decision = _relationship(role, seniority)
    email = item.get("email")
    if isinstance(email, dict):
        email_value = email.get("email") or email.get("value")
        email_status = email.get("status")
    else:
        email_value, email_status = email, None
    mobile = item.get("mobile")
    if isinstance(mobile, dict):
        mobile_value = mobile.get("mobile") or mobile.get("value")
    else:
        mobile_value = mobile
    return Person(
        full_name=name,
        role_title=role,
        relationship_type=relationship,
        linkedin_url=item.get("linkedin_url"),
        business_email=email_value,
        business_phone=mobile_value,
        is_decision_maker=decision,
        confidence=90 if email_status == "VERIFIED" else 80,
        source_url=item.get("linkedin_url"),
        raw_data={
            "provider_id": item.get("person_id"),
            "email_status": email_status,
            "seniority": seniority,
            "skills": item.get("skills") or [],
        },
    )


def _hunter_person(item: dict) -> Person | None:
    name = " ".join(
        part for part in (item.get("first_name"), item.get("last_name")) if part
    ).strip() or item.get("full_name")
    if not name:
        return None
    role = item.get("position")
    relationship, decision = _relationship(role, item.get("seniority"))
    decision = bool(item.get("decision_maker", decision))
    return Person(
        full_name=name,
        role_title=role,
        relationship_type=relationship if decision else "employee",
        linkedin_url=item.get("linkedin"),
        business_email=item.get("value") or item.get("email"),
        business_phone=item.get("phone_number"),
        is_decision_maker=decision,
        confidence=max(0, min(100, int(item.get("confidence") or 75))),
        source_url=item.get("linkedin"),
        raw_data={
            "verification_status": item.get("verification_status"),
            "department": item.get("department"),
            "seniority": item.get("seniority"),
            "type": item.get("type"),
        },
    )


def _known_people(conn, cnpj: str, limit: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT full_name,role_title,linkedin_url,is_decision_maker,confidence
            FROM intelligence.company_people
            WHERE cnpj=%s AND active=true AND source_code<>'prospeo'
            ORDER BY is_decision_maker DESC,priority_score DESC,confidence DESC
            LIMIT %s
            """,
            (cnpj, max(1, limit)),
        )
        columns = [item.name for item in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def _professional_company_signals(profile: dict, provider: str) -> list[Signal]:
    if not profile:
        return []
    signals: list[Signal] = []
    linkedin_url = profile.get("linkedin_url")
    if linkedin_url:
        signals.append(
            Signal(
                "linkedin_company_presence",
                "presence",
                "Página corporativa identificada no LinkedIn",
                6,
                90,
                source_url=linkedin_url,
                raw_data={"provider": provider, "linkedin_url": linkedin_url},
            )
        )
    employees = _as_int(
        profile.get("employee_count")
        or profile.get("estimated_num_employees")
        or profile.get("headcount")
    )
    if employees:
        score = 12 if employees >= 500 else 9 if employees >= 100 else 6 if employees >= 20 else 3
        signals.append(
            Signal(
                "professional_headcount",
                "capacity",
                f"Força profissional estimada em {employees} colaborador(es)",
                score,
                80,
                source_url=linkedin_url,
                raw_data={"provider": provider, "employee_count": employees},
            )
        )
    jobs = profile.get("job_postings") or {}
    active_jobs = _as_int(jobs.get("active_count") if isinstance(jobs, dict) else None)
    if active_jobs:
        signals.append(
            Signal(
                "active_hiring",
                "intent",
                f"Empresa com {active_jobs} vaga(s) pública(s) ativa(s)",
                min(10, 5 + active_jobs),
                85,
                expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                source_url=linkedin_url,
                raw_data={"provider": provider, "active_jobs": active_jobs},
            )
        )
    # Somente classifica vagas quando o provedor devolve título/URL reais.
    # O sinal geral de contratação não é convertido em vaga de IA/dev sem evidência.
    job_items = []
    if isinstance(jobs, dict):
        job_items = jobs.get("items") or jobs.get("jobs") or jobs.get("data") or []
    elif isinstance(jobs, list):
        job_items = jobs
    job_patterns = (
        ("ai_hiring", r"\b(ai|ia|intelig.ncia artificial|machine learning|ml engineer|automation)\b",
         "Contratação em IA/automação identificada", 20),
        ("software_hiring", r"desenvolvedor|developer|software engineer|engenheir[oa] de software|tech lead|cto|integra",
         "Contratação de tecnologia/software identificada", 15),
        ("sales_hiring", r"\b(sdr|bdr|vendedor|vendedora|gerente comercial|head comercial|sales)\b",
         "Contratação comercial identificada", 10),
    )
    for item in job_items if isinstance(job_items, list) else []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("job_title") or "").strip()
        url = item.get("url") or item.get("job_url") or linkedin_url
        if not title:
            continue
        for signal_type, pattern, label, signal_score in job_patterns:
            if re.search(pattern, title, re.I):
                signals.append(
                    Signal(
                        signal_type,
                        "intent",
                        f"{label}: {title}",
                        signal_score,
                        90,
                        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
                        source_url=url,
                        raw_data={"provider": provider, "job_id": item.get("id"), "job_title": title},
                    )
                )
    growth = profile.get("organization_headcount_six_month_growth") or profile.get("headcount_growth")
    try:
        growth_value = float(growth) if growth is not None else 0
    except (TypeError, ValueError):
        growth_value = 0
    if growth_value > 0:
        signals.append(
            Signal(
                "headcount_growth",
                "intent",
                f"Crescimento recente do quadro profissional ({growth_value:g}%)",
                min(10, 4 + round(growth_value / 10)),
                75,
                expires_at=datetime.now(timezone.utc) + timedelta(days=90),
                source_url=linkedin_url,
                raw_data={"provider": provider, "growth_percent": growth_value},
            )
        )
    funding = profile.get("funding") or {}
    total_funding = _as_int(funding.get("total_funding") if isinstance(funding, dict) else None)
    if total_funding:
        signals.append(
            Signal(
                "company_funding",
                "capacity",
                "Captação de investimento identificada",
                10,
                85,
                source_url=profile.get("crunchbase_url") or linkedin_url,
                raw_data={"provider": provider, "total_funding_usd": total_funding},
            )
        )
    social_count = _as_int(profile.get("social_count")) or sum(
        bool(profile.get(key))
        for key in ("linkedin_url", "twitter_url", "facebook_url", "instagram_url", "youtube_url")
    )
    if social_count >= 2:
        signals.append(
            Signal(
                "professional_multichannel_presence",
                "presence",
                f"Presença corporativa em {social_count} redes profissionais/digitais",
                min(8, 3 + social_count),
                80,
                source_url=linkedin_url,
                raw_data={"provider": provider, "social_channels": social_count},
            )
        )
    return signals


def _public_company_metadata(profile: dict) -> dict:
    if not profile:
        return {}
    allowed = {
        "name", "domain", "website", "linkedin_url", "industry", "employee_count",
        "estimated_num_employees", "employee_range", "founded", "revenue_range_printed",
        "description", "job_postings", "funding", "technology", "twitter_url", "facebook_url",
        "instagram_url", "youtube_url", "crunchbase_url", "organization_headcount_six_month_growth",
        "decision_makers",
    }
    return {key: value for key, value in profile.items() if key in allowed and value is not None}


def _as_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


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
    return (urlparse(base).hostname or "").removeprefix("www.") == (
        urlparse(candidate).hostname or ""
    ).removeprefix("www.")


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
