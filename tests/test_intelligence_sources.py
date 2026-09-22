from bs4 import BeautifulSoup

from cnpj_etl.intelligence.sources import _jsonld_people


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
