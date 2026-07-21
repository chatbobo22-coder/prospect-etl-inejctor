"""Carga de população municipal via APIs públicas do IBGE."""

from __future__ import annotations

import logging
import re

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

IBGE_LOCALIDADES_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/municipios"
IBGE_POPULATION_URL = (
    "https://servicodados.ibge.gov.br/api/v3/agregados/6579/periodos/{year}/variaveis/9324"
)


def rfb_municipio_code(ibge_id: int | str) -> str:
    """Código de município da Receita = 4 últimos dígitos do código IBGE (7)."""
    return str(ibge_id)[-4:].zfill(4)


def _uf_sigla(municipio: dict) -> str:
    for path in (
        ("regiao-imediata", "regiao-intermediaria", "UF", "sigla"),
        ("microrregiao", "mesorregiao", "UF", "sigla"),
    ):
        node = municipio
        for key in path:
            node = node[key]
        return node
    raise KeyError("UF não encontrada no município IBGE")


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=30))
def _get_json(url: str, *, params: dict | None = None) -> list | dict:
    response = requests.get(url, params=params, timeout=120)
    response.raise_for_status()
    return response.json()


def fetch_municipality_catalog() -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    for item in _get_json(IBGE_LOCALIDADES_URL):
        ibge_id = str(item["id"])
        catalog[ibge_id] = {
            "codigo_ibge": ibge_id,
            "uf": _uf_sigla(item),
            "codigo": rfb_municipio_code(ibge_id),
            "nome": item["nome"],
        }
    return catalog


def fetch_population_series(year: int) -> dict[str, int]:
    url = IBGE_POPULATION_URL.format(year=year)
    payload = _get_json(url, params={"localidades": "N6[all]"})
    populations: dict[str, int] = {}
    for variable in payload:
        for result in variable.get("resultados", []):
            for series in result.get("series", []):
                ibge_id = str(series["localidade"]["id"])
                raw = series.get("serie", {}).get(str(year))
                if raw is None:
                    continue
                digits = re.sub(r"\D", "", str(raw))
                if digits:
                    populations[ibge_id] = int(digits)
    return populations


def build_population_rows(year: int) -> list[dict]:
    catalog = fetch_municipality_catalog()
    populations = fetch_population_series(year)
    rows: list[dict] = []
    missing = 0
    for ibge_id, meta in catalog.items():
        populacao = populations.get(ibge_id)
        if populacao is None:
            missing += 1
            continue
        rows.append({**meta, "populacao": populacao, "ano_referencia": year})
    if missing:
        log.warning("IBGE: %s municípios sem população para %s", missing, year)
    return rows


def sync_municipios_populacao(conn, *, year: int = 2024) -> int:
    rows = build_population_rows(year)
    if not rows:
        raise RuntimeError(f"Nenhuma linha de população IBGE retornada para {year}")
    conn.executemany(
        """
        INSERT INTO cnpj.municipios_populacao
            (codigo_ibge, uf, codigo, nome, populacao, ano_referencia, updated_at)
        VALUES (%(codigo_ibge)s, %(uf)s, %(codigo)s, %(nome)s, %(populacao)s, %(ano_referencia)s, now())
        ON CONFLICT (codigo_ibge) DO UPDATE SET
            uf = EXCLUDED.uf,
            codigo = EXCLUDED.codigo,
            nome = EXCLUDED.nome,
            populacao = EXCLUDED.populacao,
            ano_referencia = EXCLUDED.ano_referencia,
            updated_at = now()
        """,
        rows,
    )
    over_min = conn.execute(
        "SELECT COUNT(*) FROM cnpj.municipios_populacao WHERE populacao >= %s",
        (100_000,),
    ).fetchone()[0]
    log.info(
        "IBGE: %s municípios sincronizados (%s com população >= 100 mil)",
        len(rows),
        over_min,
    )
    return len(rows)


def ensure_municipios_populacao(conn, *, year: int = 2024) -> int:
    count = conn.execute(
        "SELECT COUNT(*) FROM cnpj.municipios_populacao WHERE ano_referencia = %s",
        (year,),
    ).fetchone()[0]
    if count >= 5500:
        log.info("IBGE: população já carregada (%s municípios, %s)", count, year)
        return count
    return sync_municipios_populacao(conn, year=year)


def load_allowed_municipios(conn, min_population: int) -> frozenset[tuple[str, str]]:
    if min_population <= 0:
        return frozenset()
    rows = conn.execute(
        "SELECT uf, codigo FROM cnpj.municipios_populacao WHERE populacao >= %s",
        (min_population,),
    ).fetchall()
    return frozenset((row[0], row[1]) for row in rows)
