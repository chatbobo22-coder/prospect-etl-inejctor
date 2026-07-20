from cnpj_etl.source import competence_from_href, parse_nextcloud_share


def test_parse_nextcloud_share():
    origin, token, webdav = parse_nextcloud_share(
        "https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9"
    )
    assert origin == "https://arquivos.receitafederal.gov.br"
    assert token == "YggdBLfdninEJX9"
    assert webdav.endswith("/public.php/webdav/")


def test_competence_from_href():
    assert competence_from_href("2026-07") == "2026-07"
    assert competence_from_href("/public.php/webdav/2026-07/") == "2026-07"
