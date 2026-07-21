import logging
import os

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.security import APIKeyHeader

from cnpj_etl.config import Settings
from cnpj_etl.database import Database

log = logging.getLogger(__name__)
app = FastAPI(title="CNPJ ETL", version="2.0.0")

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _require_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    if os.getenv("API_REQUIRE_AUTH", "false").lower() not in {"1", "true", "yes"}:
        return
    expected = os.getenv("API_KEY", "").strip()
    if not expected or api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _service_error(status: int = 503) -> HTTPException:
    return HTTPException(status_code=status, detail="Service temporarily unavailable")


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
