from fastapi import FastAPI, HTTPException, Query

from cnpj_etl.config import Settings
from cnpj_etl.database import Database

app = FastAPI(title="CNPJ ETL", version="1.0.0")


@app.get("/")
def root():
    return {
        "service": "cnpj-etl",
        "status": "ok",
        "docs": "/docs",
        "endpoints": ["/api/health", "/api/db", "/api/runs"],
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/db")
def db_check():
    try:
        database = Database(Settings().database_url).ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "database": database}


@app.get("/api/runs")
def list_runs(limit: int = Query(default=10, ge=1, le=100)):
    try:
        db = Database(Settings().database_url)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, competence, status, started_at, finished_at, files_processed, rows_processed "
                "FROM etl.runs ORDER BY id DESC LIMIT %s",
                (limit,),
            ).fetchall()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
