from bs4 import BeautifulSoup

import requests

from cnpj_etl.intelligence.models import IntelligenceSettings
from cnpj_etl.intelligence.sources import _jsonld_people, collect_gdelt


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
    assert result.metadata["detail"] == "rate_limited"
    assert result.metadata["status_code"] == 429
