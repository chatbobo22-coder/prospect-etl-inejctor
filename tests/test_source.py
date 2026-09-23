import requests

from cnpj_etl.source import RfbSource, competence_from_href, nextcloud_webdav_roots, parse_nextcloud_share


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


def test_nextcloud_webdav_roots_prefers_current_route():
    current, legacy = nextcloud_webdav_roots("https://example.test", "share-token")
    assert current == "https://example.test/public.php/dav/files/share-token/"
    assert legacy == "https://example.test/public.php/webdav/"


def test_nextcloud_entries_falls_back_to_legacy_route(monkeypatch):
    source = RfbSource("https://example.test/index.php/s/share-token")
    calls = []

    def fake_propfind(url):
        calls.append(url)
        if "/dav/files/" in url:
            raise requests.ConnectionError("remote closed connection")
        return """<?xml version="1.0"?>
        <d:multistatus xmlns:d="DAV:">
          <d:response>
            <d:href>/public.php/webdav/2026-09/</d:href>
            <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
          </d:response>
        </d:multistatus>"""

    monkeypatch.setattr(source, "_propfind", fake_propfind)

    assert source.latest_competence() == "2026-09"
    assert calls == [
        "https://example.test/public.php/dav/files/share-token/",
        "https://example.test/public.php/webdav/",
    ]
    assert source.active_webdav_root == "https://example.test/public.php/webdav/"
