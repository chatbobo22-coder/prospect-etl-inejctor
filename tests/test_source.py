from datetime import date

import requests

from cnpj_etl.source import (
    EXPECTED_FILE_NAMES,
    RfbSource,
    competence_from_href,
    nextcloud_webdav_roots,
    parse_nextcloud_share,
    recent_competences,
)


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

    assert source._nextcloud_entries() == [("2026-09", True)]
    assert calls == [
        "https://example.test/public.php/dav/files/share-token/",
        "https://example.test/public.php/webdav/",
    ]
    assert source.active_webdav_root == "https://example.test/public.php/webdav/"


def test_recent_competences_crosses_year_boundary():
    assert recent_competences(date(2026, 2, 3), months=4) == [
        "2026-02",
        "2026-01",
        "2025-12",
        "2025-11",
    ]


def test_latest_competence_uses_current_month_without_preflight(monkeypatch):
    source = RfbSource("https://example.test/index.php/s/share-token")
    monkeypatch.setattr("cnpj_etl.source.recent_competences", lambda months: ["2026-09"])
    monkeypatch.setattr(
        source,
        "_file_exists",
        lambda _url: (_ for _ in ()).throw(AssertionError("unexpected preflight")),
    )

    assert source.latest_competence() == "2026-09"


def test_nextcloud_file_list_uses_official_37_file_contract():
    source = RfbSource("https://example.test/index.php/s/share-token")
    files = source.list_files("2026-09")

    assert len(EXPECTED_FILE_NAMES) == 37
    assert len(files) == 37
    assert files[0].name == "Cnaes.zip"
    assert files[-1].name == "Socios9.zip"
    assert all(file.file_type for file in files)


def test_current_public_dav_route_does_not_send_basic_auth():
    source = RfbSource("https://example.test/index.php/s/share-token")
    current, legacy = source.webdav_roots

    assert source._auth_for_url(current + "2026-09/Cnaes.zip") is None
    assert source._auth_for_url(legacy + "2026-09/Cnaes.zip") == ("share-token", "")


def test_curl_command_only_authenticates_legacy_route(monkeypatch):
    source = RfbSource("https://example.test/index.php/s/share-token")
    monkeypatch.setattr(source, "curl_path", "/usr/bin/curl")
    current, legacy = source.webdav_roots

    current_command = source._curl_command(current + "2026-09/Cnaes.zip")
    legacy_command = source._curl_command(legacy + "2026-09/Cnaes.zip")

    assert "--user" not in current_command
    assert legacy_command[legacy_command.index("--user") + 1] == "share-token:"


def test_curl_metadata_skips_redundant_preflight(monkeypatch):
    source = RfbSource("https://example.test/index.php/s/share-token")
    monkeypatch.setattr(source, "curl_path", "/usr/bin/curl")
    remote = source.list_files("2026-09")[0]

    monkeypatch.setattr(
        source,
        "_curl_probe",
        lambda _url: (_ for _ in ()).throw(AssertionError("unexpected preflight")),
    )

    assert source.metadata(remote) == (None, None)


def test_curl_download_requests_complete_byte_range(monkeypatch, tmp_path):
    source = RfbSource("https://example.test/index.php/s/share-token")
    monkeypatch.setattr(source, "curl_path", "/usr/bin/curl")
    remote = source.list_files("2026-09")[0]
    observed = {}

    class FakeStream:
        def read(self, _size=-1):
            return b""

    class FakeProcess:
        stdout = FakeStream()
        stderr = FakeStream()

        def wait(self):
            return 0

    def fake_popen(command, **_kwargs):
        observed["command"] = command
        return FakeProcess()

    monkeypatch.setattr("cnpj_etl.source.subprocess.Popen", fake_popen)

    source._download_with_curl(remote, str(tmp_path / "download.zip"), 1024)

    range_index = observed["command"].index("--range")
    assert observed["command"][range_index + 1] == "0-999999999999"
