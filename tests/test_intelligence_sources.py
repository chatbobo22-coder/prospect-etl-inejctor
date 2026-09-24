from bs4 import BeautifulSoup

import requests

from cnpj_etl.intelligence.models import IntelligenceSettings
from cnpj_etl.intelligence.sources import (
    _jsonld_people,
    _company_domain,
    _professional_company_signals,
    collect_gdelt,
)


def test_provider_domain_never_uses_free_email_as_company_identity():
    assert _company_domain({"email_dominio": "gmail.com", "email_tipo": "gratuito"}) is None
    assert (
        _company_domain(
            {
                "email_dominio": "gmail.com",
                "email_tipo": "gratuito",
                "site_final_url": "https://www.empresa.com.br/sobre",
            }
        )
        == "empresa.com.br"
    )


def test_jsonld_people_keeps_public_professional_fields():
    soup = BeautifulSoup(
        """
        <script type="application/ld+json">
        {"@type":"Person","name":"Ana Silva","jobTitle":"Diretora Comercial",
         "sameAs":["https://www.linkedin.com/in/ana-silva"],
         "email":"ana@empresa.com.br"}
        </script>
        """,
        "html.parser",
    )

    people = _jsonld_people(soup, "https://empresa.com.br/equipe")

    assert len(people) == 1
    assert people[0].is_decision_maker is True
    assert people[0].relationship_type == "executive"
    assert people[0].linkedin_url.endswith("/ana-silva")
    assert people[0].business_email == "ana@empresa.com.br"


def test_gdelt_timeout_is_skipped_instead_of_failing_lead(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise requests.Timeout("GDELT demorou para responder")

    monkeypatch.setattr(requests, "get", timeout)

    result = collect_gdelt(
        None,
        {"cnpj": "12345678000190", "nome_fantasia": "Empresa Exemplo"},
        IntelligenceSettings(),
    )

    assert result.status == "skipped"
    assert result.metadata["reason"] == "temporarily_unavailable"
    assert result.metadata["error_type"] == "Timeout"


def test_gdelt_rate_limit_is_recorded_as_retryable_skip(monkeypatch):
    response = requests.Response()
    response.status_code = 429
    response.url = "https://api.gdeltproject.org/api/v2/doc/doc"

    monkeypatch.setattr(requests, "get", lambda *_args, **_kwargs: response)

    result = collect_gdelt(
        None,
        {"cnpj": "12345678000190", "razao_social": "Empresa Exemplo SA"},
        IntelligenceSettings(),
    )

    assert result.status == "skipped"
    assert result.metadata["reason"] == "rate_limited"
    assert result.metadata["status_code"] == 429


def test_professional_profile_builds_linkedin_strength_and_activity_signals():
    signals = _professional_company_signals(
        {
            "linkedin_url": "https://www.linkedin.com/company/empresa-exemplo",
            "employee_count": 150,
            "job_postings": {"active_count": 4},
            "organization_headcount_six_month_growth": 12,
            "instagram_url": "https://instagram.com/empresa-exemplo",
        },
        "prospeo",
    )

    by_type = {signal.signal_type: signal for signal in signals}
    assert by_type["linkedin_company_presence"].category == "presence"
    assert by_type["professional_headcount"].category == "capacity"
    assert by_type["active_hiring"].category == "intent"
    assert by_type["headcount_growth"].score > 0
    assert by_type["professional_multichannel_presence"].category == "presence"
