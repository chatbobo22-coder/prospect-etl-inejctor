import logging
import os
from pathlib import Path
import re
import sys

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, field_validator
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cnpj_etl.config import Settings
from cnpj_etl.database import Database
from cnpj_etl.intelligence import get_company_profile, list_sources

log = logging.getLogger(__name__)
app = FastAPI(title="CNPJ ETL", version="2.0.0")

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class InjectorConfig(BaseModel):
    competence: str = ""
    cnaes: str = ""
    ufs: str = ""
    active_only: bool = True
    include_secondary_cnae: bool = False
    require_nome_fantasia: bool = True
    require_telefone: bool = True
    require_email: bool = True
    block_backoffice_email: bool = True
    min_activity_months: int = Field(default=12, ge=0, le=1200)
    min_population: int = Field(default=50000, ge=0)
    exclude_mei: bool = True
    min_confidence_score: int = Field(default=70, ge=0, le=100)
    min_lead_score: int = Field(default=60, ge=0, le=100)
    force_etl: bool = False
    force_enrich: bool = False
    enrich_batch_size: int = Field(default=500, ge=1, le=5000)
    intelligence_sources: str = (
        "receita,website,rdap,cvm,gdelt,pncp,inpi,google_places,pagespeed,"
        "meta_ads,google_ads,people_provider"
    )
    intelligence_batch_size: int = Field(default=100, ge=1, le=1000)

    @field_validator("competence")
    @classmethod
    def valid_competence(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            raise ValueError("Competência deve usar o formato YYYY-MM")
        return value

    @field_validator("cnaes")
    @classmethod
    def valid_cnaes(cls, value: str) -> str:
        values = [item.strip() for item in value.split(",") if item.strip()]
        if any(not re.fullmatch(r"\d{7}", item) for item in values):
            raise ValueError("Cada CNAE deve conter sete dígitos")
        return ",".join(dict.fromkeys(values))

    @field_validator("ufs")
    @classmethod
    def valid_ufs(cls, value: str) -> str:
        allowed = {
            "AC",
            "AL",
            "AP",
            "AM",
            "BA",
            "CE",
            "DF",
            "ES",
            "GO",
            "MA",
            "MT",
            "MS",
            "MG",
            "PA",
            "PB",
            "PR",
            "PE",
            "PI",
            "RJ",
            "RN",
            "RS",
            "RO",
            "RR",
            "SC",
            "SP",
            "SE",
            "TO",
        }
        values = [item.strip().upper() for item in value.split(",") if item.strip()]
        if any(item not in allowed for item in values):
            raise ValueError("Uma ou mais UFs são inválidas")
        return ",".join(dict.fromkeys(values))


def _require_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    if os.getenv("API_REQUIRE_AUTH", "false").lower() not in {"1", "true", "yes"}:
        return
    expected = os.getenv("API_KEY", "").strip()
    if not expected or api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _service_error(status: int = 503) -> HTTPException:
    return HTTPException(status_code=status, detail="Service temporarily unavailable")


def _github_headers(require_token: bool = False) -> dict[str, str]:
    token = os.getenv("GITHUB_WORKFLOW_TOKEN", "").strip()
    if require_token and not token:
        raise HTTPException(
            status_code=503,
            detail="GITHUB_WORKFLOW_TOKEN não configurado no Injector",
        )
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "mestrelead-injector",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_repo() -> str:
    return os.getenv("GITHUB_REPOSITORY", "chatbobo22-coder/prospect-etl-inejctor")


@app.get("/")
def root():
    return {
        "service": "cnpj-etl",
        "status": "ok",
        "docs": "/docs",
        "endpoints": ["/api/health", "/api/db", "/api/runs", "/api/enrichment/stats"],
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config", dependencies=[Depends(_require_api_key)])
def injector_config():
    settings = Settings()
    return {
        "config": {
            "source": "Receita Federal - Dados Abertos do CNPJ",
            "base_url": settings.base_url,
            "competence": "",
            "cnaes": ",".join(sorted(settings.filter_cnaes)),
            "ufs": ",".join(sorted(settings.filter_ufs)),
            "active_only": settings.filter_active_only,
            "include_secondary_cnae": settings.filter_include_secondary_cnae,
            "require_nome_fantasia": settings.filter_require_nome_fantasia,
            "require_telefone": settings.filter_require_telefone,
            "require_email": settings.filter_require_email,
            "block_backoffice_email": settings.filter_block_backoffice_email,
            "min_activity_months": settings.filter_min_activity_months,
            "min_population": settings.filter_min_population,
            "exclude_mei": os.getenv("PROSPECT_EXCLUDE_MEI", "true").lower()
            in {"1", "true", "yes", "on"},
            "min_confidence_score": int(
                os.getenv("PROSPECT_MIN_CONFIDENCE_SCORE", "70")
            ),
            "min_lead_score": int(os.getenv("PROSPECT_MIN_LEAD_SCORE", "60")),
            "force_etl": False,
            "force_enrich": False,
            "enrich_batch_size": int(os.getenv("ENRICH_BATCH_SIZE", "500")),
            "intelligence_sources": os.getenv(
                "INTELLIGENCE_SOURCES",
                "receita,website,rdap,cvm,gdelt,pncp,inpi,google_places,pagespeed,meta_ads,google_ads,people_provider",
            ),
            "intelligence_batch_size": int(os.getenv("INTELLIGENCE_BATCH_SIZE", "100")),
        }
    }


@app.post("/api/start", dependencies=[Depends(_require_api_key)], status_code=202)
def start_injector(config: InjectorConfig):
    workflow = os.getenv("GITHUB_WORKFLOW_FILE", "prospect-pipeline.yml")
    response = requests.post(
        f"https://api.github.com/repos/{_github_repo()}/actions/workflows/{workflow}/dispatches",
        headers=_github_headers(require_token=True),
        json={
            "ref": os.getenv("GITHUB_WORKFLOW_REF", "main"),
            "inputs": {
                "competence": config.competence,
                "filter_cnaes": config.cnaes,
                "filter_ufs": config.ufs,
                "active_only": str(config.active_only).lower(),
                "include_secondary_cnae": str(config.include_secondary_cnae).lower(),
                "require_nome_fantasia": str(config.require_nome_fantasia).lower(),
                "require_telefone": str(config.require_telefone).lower(),
                "require_email": str(config.require_email).lower(),
                "block_backoffice_email": str(config.block_backoffice_email).lower(),
                "min_activity_months": str(config.min_activity_months),
                "min_population": str(config.min_population),
                "exclude_mei": str(config.exclude_mei).lower(),
                "min_confidence_score": str(config.min_confidence_score),
                "min_lead_score": str(config.min_lead_score),
                "force_etl": str(config.force_etl).lower(),
                "force_enrich": str(config.force_enrich).lower(),
                "enrich_batch_size": str(config.enrich_batch_size),
                "intelligence_sources": config.intelligence_sources,
                "intelligence_batch_size": str(config.intelligence_batch_size),
            },
        },
        timeout=20,
    )
    if response.status_code != 204:
        log.error("GitHub workflow dispatch failed: %s %s", response.status_code, response.text)
        raise HTTPException(status_code=502, detail="Não foi possível iniciar o Injector")
    return {"started": True, "workflow": workflow}


@app.get("/api/workflow-runs", dependencies=[Depends(_require_api_key)])
def workflow_runs(limit: int = Query(default=10, ge=1, le=50)):
    workflow = os.getenv("GITHUB_WORKFLOW_FILE", "prospect-pipeline.yml")
    response = requests.get(
        f"https://api.github.com/repos/{_github_repo()}/actions/workflows/{workflow}/runs",
        headers=_github_headers(),
        params={"per_page": limit},
        timeout=20,
    )
    if not response.ok:
        raise HTTPException(status_code=502, detail="Não foi possível consultar o Injector")
    data = response.json()
    return {
        "runs": [
            {
                "id": item["id"],
                "status": item["status"],
                "conclusion": item.get("conclusion"),
                "event": item["event"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
                "html_url": item["html_url"],
                "head_sha": item["head_sha"],
            }
            for item in data.get("workflow_runs", [])
        ]
    }


@app.get("/api/db", dependencies=[Depends(_require_api_key)])
def db_check():
    try:
        database = Database(Settings().database_url).ping()
    except Exception:
        log.exception("DB check failed")
        raise _service_error() from None
    return {"status": "ok", "database": database}


@app.get("/api/runs", dependencies=[Depends(_require_api_key)])
def list_runs(limit: int = Query(default=10, ge=1, le=100)):
    try:
        db = Database(Settings().database_url)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, competence, status, started_at, finished_at, files_processed, rows_processed "
                "FROM etl.runs ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()
    except Exception:
        log.exception("List runs failed")
        raise _service_error() from None
    return {
        "runs": [
            {
                "id": r[0],
                "competence": r[1],
                "status": r[2],
                "started_at": r[3].isoformat() if r[3] else None,
                "finished_at": r[4].isoformat() if r[4] else None,
                "files_processed": r[5],
                "rows_processed": r[6],
            }
            for r in rows
        ]
    }


@app.get("/api/enrichment/stats", dependencies=[Depends(_require_api_key)])
def enrichment_stats():
    try:
        db = Database(Settings().database_url)
        with db.connect() as conn:
            by_status = conn.execute(
                """
                SELECT enrich_status, COUNT(*) FROM cnpj.digital_presenca GROUP BY 1
                """
            ).fetchall()
            sites_valid = conn.execute(
                "SELECT COUNT(*) FROM cnpj.digital_presenca WHERE site_valid = true"
            ).fetchone()[0]
            whatsapp_valid = conn.execute(
                "SELECT COUNT(*) FROM cnpj.digital_presenca WHERE whatsapp_valid = true"
            ).fetchone()[0]
            commerce = conn.execute(
                """
                SELECT commerce_maturity, COUNT(*) FROM cnpj.digital_presenca
                WHERE commerce_maturity IS NOT NULL GROUP BY 1
                """
            ).fetchall()
            presence = conn.execute(
                """
                SELECT presence_maturity, COUNT(*) FROM cnpj.digital_presenca
                WHERE presence_maturity IS NOT NULL GROUP BY 1
                """
            ).fetchall()
            leads = conn.execute(
                """
                SELECT lead_classification, COUNT(*) FROM cnpj.digital_presenca
                WHERE lead_classification IS NOT NULL GROUP BY 1
                """
            ).fetchall()
            platforms = conn.execute(
                """
                SELECT plataforma, COUNT(*) FROM cnpj.digital_presenca
                WHERE plataforma IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 20
                """
            ).fetchall()
            qual = conn.execute(
                """
                SELECT qualification_status, COUNT(*) FROM cnpj.prospectos_qualificados
                GROUP BY 1
                """
            ).fetchall()
    except Exception:
        log.exception("Enrichment stats failed")
        raise _service_error() from None

    return {
        "enrich_status": {row[0]: row[1] for row in by_status},
        "sites_valid": sites_valid,
        "whatsapps_valid": whatsapp_valid,
        "commerce_maturity": {row[0]: row[1] for row in commerce},
        "presence_maturity": {row[0]: row[1] for row in presence},
        "lead_classification": {row[0]: row[1] for row in leads},
        "platforms": {row[0]: row[1] for row in platforms},
        "qualification_status": {row[0]: row[1] for row in qual},
    }


@app.get("/api/intelligence/sources", dependencies=[Depends(_require_api_key)])
def intelligence_sources():
    try:
        db = Database(Settings().database_url)
        with db.connect() as conn:
            sources = list_sources(conn)
    except Exception:
        log.exception("Intelligence source list failed")
        raise _service_error() from None
    return {"sources": sources}


@app.get("/api/intelligence/companies/{cnpj}", dependencies=[Depends(_require_api_key)])
def intelligence_company(cnpj: str):
    digits = re.sub(r"\D", "", cnpj)
    if len(digits) != 14:
        raise HTTPException(status_code=422, detail="CNPJ inválido")
    try:
        db = Database(Settings().database_url)
        with db.connect() as conn:
            profile = get_company_profile(conn, digits)
    except Exception:
        log.exception("Intelligence profile failed")
        raise _service_error() from None
    if not profile:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return profile
