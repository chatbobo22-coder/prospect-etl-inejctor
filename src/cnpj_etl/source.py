from dataclasses import dataclass
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential


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


def classify(name: str) -> str | None:
    return next((kind for kind, pattern in TYPE_PATTERNS.items() if pattern.search(name)), None)


class RfbSource:
    def __init__(self, base_url: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "cnpj-etl/1.0 (dados-abertos)"

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30), reraise=True)
    def _get(self, url: str, *, stream: bool = False):
        response = self.session.get(url, timeout=self.timeout, stream=stream)
        response.raise_for_status()
        return response

    def latest_competence(self) -> str:
        soup = BeautifulSoup(self._get(self.base_url).text, "html.parser")
        values = []
        for a in soup.select("a[href]"):
            match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])/?", a.get("href", ""))
            if match:
                values.append(f"{match.group(1)}-{match.group(2)}")
        if not values:
            raise RuntimeError(
                "Nenhuma competência YYYY-MM encontrada na página da Receita Federal"
            )
        return max(values)

    def list_files(self, competence: str) -> list[RemoteFile]:
        page = urljoin(self.base_url, competence + "/")
        soup = BeautifulSoup(self._get(page).text, "html.parser")
        result = []
        for a in soup.select("a[href]"):
            name = a.get("href", "").split("/")[-1]
            kind = classify(name)
            if name.lower().endswith(".zip") and kind:
                result.append(RemoteFile(competence, name, urljoin(page, name), kind))
        if not result:
            raise RuntimeError(f"Nenhum ZIP reconhecido em {page}")
        return sorted(result, key=lambda item: item.name)

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(min=2, max=30), reraise=True)
    def metadata(self, remote: RemoteFile) -> tuple[int | None, str | None]:
        response = self.session.head(remote.url, timeout=self.timeout, allow_redirects=True)
        response.raise_for_status()
        size = response.headers.get("Content-Length")
        return (
            int(size) if size and size.isdigit() else None,
            response.headers.get("Last-Modified"),
        )

    def download(self, remote: RemoteFile, destination, chunk_bytes: int) -> tuple[str, int]:
        import hashlib

        temporary = destination.with_suffix(destination.suffix + ".part")
        digest, size = hashlib.sha256(), 0
        with self._get(remote.url, stream=True) as response, temporary.open("wb") as output:
            for chunk in response.iter_content(chunk_size=chunk_bytes):
                if chunk:
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        temporary.replace(destination)
        return digest.hexdigest(), size
