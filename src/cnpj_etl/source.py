import hashlib
import logging
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
import re
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

DOWNLOAD_LOG_EVERY_BYTES = 100 * 1024 * 1024  # 100 MB


def fmt_bytes(num: int) -> str:
    if num >= 1024 * 1024 * 1024:
        return f"{num / (1024**3):.1f} GB"
    if num >= 1024 * 1024:
        return f"{num / (1024**2):.1f} MB"
    if num >= 1024:
        return f"{num / 1024:.1f} KB"
    return f"{num} B"


@dataclass(frozen=True)
class RemoteFile:
    competence: str
    name: str
    url: str
    file_type: str


TYPE_PATTERNS = {
    "Empresas": re.compile(r"Empresas", re.I),
    "Estabelecimentos": re.compile(r"Estabelecimentos", re.I),
    "Socios": re.compile(r"Socios", re.I),
    "Simples": re.compile(r"Simples", re.I),
    "Cnaes": re.compile(r"Cnaes", re.I),
    "Municipios": re.compile(r"Municipios", re.I),
    "Paises": re.compile(r"Paises", re.I),
    "Naturezas": re.compile(r"Naturezas", re.I),
    "Qualificacoes": re.compile(r"Qualificacoes", re.I),
    "Motivos": re.compile(r"Motivos", re.I),
}

NEXTCLOUD_SHARE_RE = re.compile(r"^(?P<origin>https?://[^/]+)/index\.php/s/(?P<token>[^/?#]+)")
COMPETENCE_RE = re.compile(r"(20\d{2})-(0[1-9]|1[0-2])")
WEBDAV_NS = {"d": "DAV:"}


def classify(name: str) -> str | None:
    return next((kind for kind, pattern in TYPE_PATTERNS.items() if pattern.search(name)), None)


def parse_nextcloud_share(base_url: str) -> tuple[str, str, str] | None:
    match = NEXTCLOUD_SHARE_RE.match(base_url.rstrip("/"))
    if not match:
        return None
    origin = match.group("origin")
    token = match.group("token")
    webdav_root = f"{origin}/public.php/webdav/"
    return origin, token, webdav_root


def nextcloud_webdav_roots(origin: str, token: str) -> tuple[str, str]:
    """Return the current Nextcloud DAV route and its legacy compatibility route."""
    return (
        f"{origin}/public.php/dav/files/{token}/",
        f"{origin}/public.php/webdav/",
    )


def competence_from_href(href: str) -> str | None:
    match = COMPETENCE_RE.search(href)
    return match.group(0) if match else None


def entries_from_propfind(xml_text: str) -> list[tuple[str, bool]]:
    root = ET.fromstring(xml_text)
    entries: list[tuple[str, bool]] = []
    for response in root.findall("d:response", WEBDAV_NS):
        href_el = response.find("d:href", WEBDAV_NS)
        if href_el is None or not href_el.text:
            continue
        href = href_el.text
        name = href.rstrip("/").split("/")[-1]
        if not name:
            continue
        resource_type = response.find(".//d:resourcetype", WEBDAV_NS)
        is_dir = (
            resource_type is not None and resource_type.find("d:collection", WEBDAV_NS) is not None
        )
        entries.append((name, is_dir))
    return entries


class RfbSource:
    def __init__(self, base_url: str, timeout: int = 120):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "cnpj-etl/1.0 (dados-abertos)"
        parsed = parse_nextcloud_share(base_url)
        if parsed:
            self.origin, self.token, self.webdav_root = parsed
            self.webdav_roots = nextcloud_webdav_roots(self.origin, self.token)
            self.active_webdav_root = self.webdav_roots[0]
            self.mode = "nextcloud"
            self.base_url = base_url.rstrip("/")
        else:
            self.mode = "html"
            self.base_url = base_url.rstrip("/") + "/"
            self.origin = self.token = self.webdav_root = ""

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30), reraise=True)
    def _get(self, url: str, *, stream: bool = False, auth: tuple[str, str] | None = None):
        response = self.session.get(url, timeout=self.timeout, stream=stream, auth=auth)
        response.raise_for_status()
        return response

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(min=2, max=30),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def _propfind(self, url: str) -> str:
        # The Receita Nextcloud occasionally closes pooled connections from
        # GitHub-hosted runners. A short-lived connection avoids reusing a
        # socket that the remote server has already discarded.
        with requests.Session() as session:
            session.headers.update(self.session.headers)
            response = session.request(
                "PROPFIND",
                url,
                auth=(self.token, ""),
                headers={
                    "Accept": "application/xml, text/xml;q=0.9, */*;q=0.8",
                    "Connection": "close",
                    "Depth": "1",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.text

    def _nextcloud_entries(self, path: str = "") -> list[tuple[str, bool]]:
        last_error: Exception | None = None
        roots = (self.active_webdav_root,) + tuple(
            root for root in self.webdav_roots if root != self.active_webdav_root
        )
        for root in roots:
            url = urljoin(root, path)
            try:
                entries = entries_from_propfind(self._propfind(url))
            except (requests.RequestException, ET.ParseError) as exc:
                last_error = exc
                log.warning("Rota WebDAV indisponível (%s); tentando alternativa", root)
                continue
            self.active_webdav_root = root
            return entries
        if last_error:
            raise last_error
        raise RuntimeError("Nenhuma rota WebDAV disponível para a fonte da Receita Federal")

    def _absolute_url(self, href: str) -> str:
        if href.startswith("http"):
            return href
        return urljoin(self.origin, href)

    def latest_competence(self) -> str:
        if self.mode == "nextcloud":
            values = sorted(
                {
                    competence
                    for name, is_dir in self._nextcloud_entries()
                    if is_dir and (competence := competence_from_href(name))
                }
            )
        else:
            soup = BeautifulSoup(self._get(self.base_url).text, "html.parser")
            values = []
            for a in soup.select("a[href]"):
                match = COMPETENCE_RE.fullmatch(a.get("href", "").strip("/"))
                if match:
                    values.append(match.group(0))
        if not values:
            raise RuntimeError("Nenhuma competência YYYY-MM encontrada na fonte da Receita Federal")
        return values[-1]

    def list_files(self, competence: str) -> list[RemoteFile]:
        if self.mode == "nextcloud":
            result = []
            for name, is_dir in self._nextcloud_entries(f"{competence}/"):
                if is_dir:
                    continue
                kind = classify(name)
                if name.lower().endswith(".zip") and kind:
                    result.append(
                        RemoteFile(
                            competence,
                            name,
                            urljoin(self.active_webdav_root, f"{competence}/{name}"),
                            kind,
                        )
                    )
        else:
            page = urljoin(self.base_url, competence + "/")
            soup = BeautifulSoup(self._get(page).text, "html.parser")
            result = []
            for a in soup.select("a[href]"):
                name = a.get("href", "").split("/")[-1]
                kind = classify(name)
                if name.lower().endswith(".zip") and kind:
                    result.append(RemoteFile(competence, name, urljoin(page, name), kind))
        if not result:
            raise RuntimeError(f"Nenhum ZIP reconhecido para a competência {competence}")
        return sorted(result, key=lambda item: item.name)

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30), reraise=True)
    def metadata(self, remote: RemoteFile) -> tuple[int | None, str | None]:
        auth = (self.token, "") if self.mode == "nextcloud" else None
        response = self.session.head(
            remote.url, timeout=self.timeout, allow_redirects=True, auth=auth
        )
        response.raise_for_status()
        size = response.headers.get("Content-Length")
        return (
            int(size) if size and size.isdigit() else None,
            response.headers.get("Last-Modified"),
        )

    def _download_to_path(
        self, remote: RemoteFile, destination: str, chunk_bytes: int
    ) -> tuple[str, int]:
        digest, size = hashlib.sha256(), 0
        last_logged = 0
        auth = (self.token, "") if self.mode == "nextcloud" else None
        log.info("Download iniciado: %s", remote.name)
        with (
            self._get(remote.url, stream=True, auth=auth) as response,
            open(destination, "wb") as output,
        ):
            expected = response.headers.get("Content-Length")
            if expected and expected.isdigit():
                log.info("Download %s: tamanho esperado %s", remote.name, fmt_bytes(int(expected)))
            for chunk in response.iter_content(chunk_size=chunk_bytes):
                if chunk:
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    if size - last_logged >= DOWNLOAD_LOG_EVERY_BYTES:
                        log.info("Download %s: %s recebidos", remote.name, fmt_bytes(size))
                        last_logged = size
        log.info("Download concluído: %s (%s)", remote.name, fmt_bytes(size))
        return digest.hexdigest(), size

    def download(self, remote: RemoteFile, destination, chunk_bytes: int) -> tuple[str, int]:
        temporary = destination.with_suffix(destination.suffix + ".part")
        sha256, size = self._download_to_path(remote, temporary, chunk_bytes)
        temporary.replace(destination)
        return sha256, size

    @contextmanager
    def temporary_download(self, remote: RemoteFile, chunk_bytes: int):
        fd, path = tempfile.mkstemp(prefix="cnpj-etl-", suffix=".zip")
        os.close(fd)
        try:
            sha256, size = self._download_to_path(remote, path, chunk_bytes)
            yield path, sha256, size
        finally:
            if os.path.exists(path):
                os.unlink(path)
