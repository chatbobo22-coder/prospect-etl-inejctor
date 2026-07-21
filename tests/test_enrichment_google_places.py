from cnpj_etl.enrichment.google_places import GooglePlacesClient, score_place_match
from cnpj_etl.enrichment.models import EnrichSettings


def test_place_match_by_name_and_city():
    place = {
        "displayName": {"text": "Loja Moda Center"},
        "formattedAddress": "Rua A, Curitiba - PR, 80000",
    }
    score = score_place_match(
        place,
        nome_fantasia="Moda Center",
        razao_social="Moda Center LTDA",
        municipio="Curitiba",
        uf="PR",
        telefone="41999999999",
        endereco="Rua A",
        cep="80000000",
        cnae="4751201",
    )
    assert score >= 50


def test_different_city_penalized():
    place = {
        "displayName": {"text": "Loja Moda Center"},
        "formattedAddress": "Rua A, São Paulo - SP",
    }
    score = score_place_match(
        place,
        nome_fantasia="Moda Center",
        razao_social="Moda Center LTDA",
        municipio="Curitiba",
        uf="PR",
        telefone=None,
        endereco=None,
        cep=None,
        cnae="4751201",
    )
    assert score < 70


def test_client_disabled_without_key():
    settings = EnrichSettings(google_places_enabled=True, google_places_api_key="")
    client = GooglePlacesClient(settings)
    assert client.enabled is False
    assert client.search("Loja") == []
