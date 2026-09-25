import logging
import os
from pathlib import Path
import re
import sys
from datetime import datetime
import hmac
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, field_validator
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cnpj_etl.config import Settings
from cnpj_etl.database import Database
from cnpj_etl.intelligence import get_company_profile, list_sources
from cnpj_etl.intelligence.pipeline import record_feedback
from cnpj_etl.intent.service import get_opportunity, list_opportunities, rebuild_profiles

log = logging.getLogger(__name__)
app = FastAPI(title="CNPJ ETL", version="2.0.0")

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class InjectorConfig(BaseModel):
    competence: str = ""
    cnaes: str = ""
    ufs: str = ""
    active_only: bool = True
    include_secondary_cnae: bool = True
    require_nome_fantasia: bool = False
    require_telefone: bool = False
    require_email: bool = True
    block_backoffice_email: bool = True
    min_activity_months: int = Field(default=12, ge=0, le=1200)
    min_population: int = Field(default=0, ge=0)
    exclude_mei: bool = True
    min_confidence_score: int = Field(default=70, ge=0, le=100)
    min_lead_score: int = Field(default=70, ge=0, le=100)
    load_batch_size: int = Field(default=100000, ge=1000, le=250000)
    force_etl: bool = True
    continuous: bool = True
    force_enrich: bool = False
    enrich_batch_size: int = Field(default=2000, ge=1, le=5000)
    intelligence_sources: str = (
        "receita,email_quality,website,rdap,cvm,gdelt,pncp,inpi,google_places,pagespeed,"
        "meta_ads,google_ads,people_provider"
    )
    intelligence_batch_size: int = Field(default=250, ge=1, le=1000)

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


class CommercialFeedback(BaseModel):
    cnpj: str
    outcome: Literal[
        "attempted",
        "delivered",
        "opened",
        "clicked",
        "replied_positive",
        "replied_negative",
        "meeting_scheduled",
        "opportunity_created",
        "won",
        "lost",
        "bounced",
        "unsubscribed",
        "wrong_contact",
    ]
    person_id: int | None = None
    channel: str | None = None
    campaign_id: str | None = None
    source: str = "mestrelead"
    external_id: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None

    @field_validator("cnpj")
    @classmethod
    def valid_cnpj(cls, value: str) -> str:
        digits = re.sub(r"\D", "", value)
        if len(digits) != 14:
            raise ValueError("CNPJ inválido")
        return digits


def _require_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    if os.getenv("API_REQUIRE_AUTH", "false").lower() not in {"1", "true", "yes"}:
        return
    expected = os.getenv("API_KEY", "").strip()
    if not expected or api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _require_write_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    expected = os.getenv("API_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="API_KEY não configurada")
    if not api_key or not hmac.compare_digest(api_key, expected):
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


def _workflow_progress(status: str, steps: list[dict[str, Any]]) -> int:
    if status == "completed":
        return 100
    if not steps:
        return 2 if status in {"queued", "waiting", "requested", "pending"} else 5
    completed = sum(step.get("status") == "completed" for step in steps)
    active = any(step.get("status") == "in_progress" for step in steps)
    progress = ((completed + (0.25 if active else 0)) / len(steps)) * 100
    return max(1, min(99, round(progress)))


def _sanitize_log_lines(raw: str, limit: int = 500) -> list[str]:
    cleaned: list[str] = []
    ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    secret = re.compile(r"(?i)(password|secret|token|api[_-]?key|database_url)\s*[=:]\s*\S+")
    database_url = re.compile(r"(?i)postgres(?:ql)?://\S+")
    for line in raw.splitlines():
        line = ansi.sub("", line).strip()
        line = database_url.sub("postgresql://***", line)
        line = secret.sub(lambda match: f"{match.group(1)}=***", line)
        if line:
            cleaned.append(line[-1200:])
    return cleaned[-limit:]


def _format_runtime_bytes(value: int | None) -> str:
    size = float(value or 0)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value or 0)} B"


def _runtime_log_lines(
    run: dict[str, Any],
    steps: list[dict[str, Any]],
    telemetry: dict[str, Any],
    github_lines: list[str],
) -> list[str]:
    """Combina log bruto e telemetria em um fluxo legível, com o estado atual no fim."""
    lines = list(github_lines)
    if not lines:
        lines.extend(
            f"[{step['status'].upper()}] {step['name']}"
            for step in steps
            if step["status"] in {"completed", "in_progress"}
        )

    for item in reversed(telemetry.get("files", [])):
        timestamp = item.get("processed_at") or item.get("downloaded_at") or ""
        details = [
            f"tipo={item.get('type') or 'não informado'}",
            f"linhas={int(item.get('rows') or 0):,}",
        ]
        if item.get("bytes"):
            details.append(f"tamanho={_format_runtime_bytes(item['bytes'])}")
        if item.get("downloaded_bytes"):
            details.append(f"baixados={_format_runtime_bytes(item['downloaded_bytes'])}")
        if item.get("scanned_rows"):
            details.append(f"lidas={int(item['scanned_rows']):,}")
            details.append(f"ignoradas={int(item.get('skipped_rows') or 0):,}")
        if item.get("error"):
            details.append(f"erro={item['error']}")
        lines.append(
            f"{timestamp} [ARQUIVO:{str(item.get('status') or 'pending').upper()}] "
            f"{item.get('name') or 'arquivo'} | {' | '.join(details)}"
        )

    etl_run = telemetry.get("etl_run")
    if etl_run:
        lines.append(
            "[ETL] "
            f"competência={etl_run.get('competence') or 'automática'} | "
            f"status={etl_run.get('status') or 'aguardando'} | "
            f"arquivos={int(etl_run.get('files_processed') or 0)}/"
            f"{int(etl_run.get('files_total') or 0)} | "
            f"linhas={int(etl_run.get('rows_processed') or 0):,}"
        )
        if etl_run.get("error"):
            lines.append(f"[ERRO:ETL] {etl_run['error']}")

    counts = telemetry.get("counts") or {}
    lines.append(
        "[BASE] "
        f"empresas={int(counts.get('companies') or 0):,} | "
        f"enriquecidas={int(counts.get('enriched') or 0):,} | "
        f"qualificadas A/B={int(counts.get('qualified') or 0):,} | "
        f"descartadas={int(counts.get('rejected') or 0):,}"
    )
    lines.append(
        "[TRIAGEM] "
        f"pré-filtro barato={int(counts.get('rejected_pre_enrichment') or 0):,} | "
        f"após presença digital={int(counts.get('rejected_below_score') or 0):,} | "
        f"outros motivos={max(0, int(counts.get('rejected') or 0) - int(counts.get('rejected_pre_enrichment') or 0) - int(counts.get('rejected_below_score') or 0)):,}"
    )

    intelligence = telemetry.get("intelligence") or {}
    if intelligence:
        source = intelligence.get("current_source") or intelligence.get("latest_source")
        details = [
            f"consultas concluídas={int(intelligence.get('completed_checks') or 0):,}",
            f"perfis={int(intelligence.get('profiles') or 0):,}",
            f"falhas={int(intelligence.get('failed_checks') or 0):,}",
        ]
        if source:
            details.append(f"fonte={source}")
        lines.append(f"[INTELIGÊNCIA] {' | '.join(details)}")

    enrichment = telemetry.get("enrichment") or {}
    if enrichment:
        lines.append(
            "[ENRIQUECIMENTO] "
            f"status={enrichment.get('status') or 'aguardando'} | "
            f"processados={int(enrichment.get('processed') or 0):,} | "
            f"concluídos={int(enrichment.get('done') or 0):,} | "
            f"parciais={int(enrichment.get('partial') or 0):,} | "
            f"sem site={int(enrichment.get('no_site') or 0):,} | "
            f"falhas={int(enrichment.get('failed') or 0):,}"
        )

    storage = telemetry.get("storage") or {}
    schema_parts = [
        f"{name}={_format_runtime_bytes(size)}"
        for name, size in sorted((storage.get("schemas") or {}).items())
    ]
    storage_line = f"[ARMAZENAMENTO] banco={_format_runtime_bytes(storage.get('database_bytes'))}"
    if schema_parts:
        storage_line += " | " + " | ".join(schema_parts)
    lines.append(storage_line)

    if telemetry.get("warning"):
        lines.append(f"[AVISO] {telemetry['warning']}")

    activity = telemetry.get("activity") or {}
    if activity:
        progress = ""
        if activity.get("total"):
            progress = (
                f" | avanço={int(activity.get('current') or 0):,}/"
                f"{int(activity['total']):,}"
            )
        lines.append(
            f"[ATIVIDADE] {activity.get('label') or activity.get('phase') or 'Processando'}"
            f"{progress} | último sinal={activity.get('updated_at') or 'agora'}"
        )

    current_step = next(
        (step["name"] for step in steps if step["status"] == "in_progress"),
        "Concluído" if run.get("status") == "completed" else "Aguardando executor",
    )
    lines.append(
        f"[AGORA] {current_step} | execução={run.get('status') or 'desconhecido'} | "
        f"atualizado={run.get('updated_at') or ''}"
    )
    return _sanitize_log_lines("\n".join(lines), limit=500)


def _github_run(run_id: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    base = f"https://api.github.com/repos/{_github_repo()}/actions"
    run_response = requests.get(f"{base}/runs/{run_id}", headers=_github_headers(), timeout=20)
    if run_response.status_code == 404:
        raise HTTPException(status_code=404, detail="Execução não encontrada")
    if not run_response.ok:
        raise HTTPException(status_code=502, detail="Não foi possível consultar a execução")
    jobs_response = requests.get(
        f"{base}/runs/{run_id}/jobs",
        headers=_github_headers(),
        params={"per_page": 20},
        timeout=20,
    )
    if not jobs_response.ok:
        raise HTTPException(status_code=502, detail="Não foi possível consultar as etapas")
    jobs = jobs_response.json().get("jobs", [])
    log_lines: list[str] = []
    active_job = next(
        (job for job in jobs if job.get("status") == "in_progress"),
        jobs[-1] if jobs else None,
    )
    if active_job and os.getenv("GITHUB_WORKFLOW_TOKEN", "").strip():
        try:
            log_response = requests.get(
                f"{base}/jobs/{active_job['id']}/logs",
                headers=_github_headers(require_token=True),
                timeout=20,
            )
            if log_response.ok:
                log_lines = _sanitize_log_lines(log_response.text)
        except requests.RequestException:
            log.warning("GitHub job logs unavailable for run %s", run_id)
    return run_response.json(), jobs, log_lines


def _database_telemetry(workflow_run_id: int) -> dict[str, Any]:
    db = Database(Settings().database_url)
    with db.connect() as conn:
        has_workflow_column = conn.execute(
            """
            SELECT EXISTS (
              SELECT 1 FROM information_schema.columns
              WHERE table_schema='etl' AND table_name='runs'
                AND column_name='workflow_run_id'
            )
            """
        ).fetchone()[0]
        if has_workflow_column:
            etl_run = conn.execute(
                """
                SELECT id,competence,status,started_at,finished_at,files_total,
                       files_processed,rows_processed,error_message,cancel_requested_at
                FROM etl.runs WHERE workflow_run_id=%s ORDER BY id DESC LIMIT 1
                """,
                (workflow_run_id,),
            ).fetchone()
        else:
            etl_run = None
        database_bytes = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        schema_rows = conn.execute(
            """
            SELECT schemaname,COALESCE(sum(pg_total_relation_size(relid)),0)::bigint
            FROM pg_catalog.pg_statio_user_tables
            WHERE schemaname IN ('cnpj','etl','intelligence')
            GROUP BY schemaname ORDER BY 2 DESC
            """
        ).fetchall()
        archived_decisions = (0, 0, 0)
        if conn.execute(
            "SELECT to_regclass('etl.funnel_metrics') IS NOT NULL"
        ).fetchone()[0]:
            archived_decisions = conn.execute(
                """
                SELECT rejected,rejected_below_score,rejected_pre_enrichment
                FROM etl.funnel_metrics WHERE singleton
                """
            ).fetchone() or archived_decisions
        counts = conn.execute(
            """
            WITH decision_counts AS (
              SELECT
                count(*) FILTER (WHERE decision='rejected') AS rejected,
                count(*) FILTER (
                    WHERE decision='rejected'
                      AND EXISTS (
                        SELECT 1 FROM unnest(reason_codes) AS reasons(reason)
                        WHERE reason LIKE 'lead_score_abaixo_%'
                           OR reason LIKE 'score_digital_abaixo_%'
                      )
                  ) AS rejected_below_score,
                count(*) FILTER (
                    WHERE decision='rejected'
                      AND EXISTS (
                        SELECT 1 FROM unnest(reason_codes) AS reasons(reason)
                        WHERE reason LIKE 'pre_score%abaixo_%'
                      )
                  ) AS rejected_pre_enrichment
              FROM etl.candidate_decisions
            )
            SELECT
              (SELECT count(*) FROM cnpj.estabelecimentos),
              (SELECT count(*) FROM cnpj.digital_presenca),
              (SELECT count(*) FROM cnpj.prospectos_qualificados
                WHERE qualification_status='qualified'
                  AND lead_quality IN ('A','B')),
              rejected,
              rejected_below_score,
              rejected_pre_enrichment
            FROM decision_counts
            """
        ).fetchone()
        counts = (
            counts[0],counts[1],counts[2],
            counts[3] + archived_decisions[0],
            counts[4] + archived_decisions[1],
            counts[5] + archived_decisions[2],
        )
        source_rows = conn.execute(
            """
            SELECT source_code,
                   count(*) FILTER (WHERE status IN ('success','no_data','skipped'))::bigint,
                   count(*) FILTER (WHERE status='failed')::bigint,
                   count(*) FILTER (WHERE status='running')::bigint,
                   count(*)::bigint
            FROM intelligence.company_source_state
            GROUP BY source_code
            ORDER BY source_code
            """
        ).fetchall()
        profiles = conn.execute("SELECT count(*) FROM intelligence.company_profiles").fetchone()[0]
        latest_source_run = conn.execute(
            """
            SELECT source_code,status,processed,success,no_data,failed,
                   started_at,finished_at,
                   COALESCE((to_jsonb(sr)->>'activity_at')::timestamptz,started_at)
            FROM intelligence.source_runs sr
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        latest_enrichment_run = conn.execute(
            """
            SELECT id,status,started_at,finished_at,processed,done,partial,no_site,failed,
                   COALESCE((to_jsonb(er)->>'activity_at')::timestamptz,started_at)
            FROM etl.enrichment_runs er
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        files = []
        if etl_run:
            file_rows = conn.execute(
                """
                SELECT file_name,file_type,status,rows_processed,source_size,
                       downloaded_at,processed_at,error_message,
                       COALESCE((to_jsonb(f)->>'downloaded_bytes')::bigint,0),
                       COALESCE((to_jsonb(f)->>'scanned_rows')::bigint,0),
                       COALESCE((to_jsonb(f)->>'skipped_rows')::bigint,0),
                       COALESCE(
                         (to_jsonb(f)->>'activity_at')::timestamptz,
                         processed_at,downloaded_at
                       )
                FROM etl.files f WHERE competence=%s
                ORDER BY (status IN ('downloading','processing')) DESC,
                         COALESCE(
                           (to_jsonb(f)->>'activity_at')::timestamptz,
                           processed_at,downloaded_at
                         ) DESC NULLS LAST,
                         file_name
                LIMIT 20
                """,
                (etl_run[1],),
            ).fetchall()
            files = [
                {
                    "name": row[0],
                    "type": row[1],
                    "status": row[2],
                    "rows": row[3],
                    "bytes": row[4],
                    "downloaded_at": row[5].isoformat() if row[5] else None,
                    "processed_at": row[6].isoformat() if row[6] else None,
                    "error": row[7],
                    "downloaded_bytes": row[8],
                    "scanned_rows": row[9],
                    "skipped_rows": row[10],
                    "activity_at": row[11].isoformat() if row[11] else None,
                }
                for row in file_rows
            ]
    storage_limit_mb = int(os.getenv("DATABASE_STORAGE_LIMIT_MB", "0") or 0)
    run_data = None
    if etl_run:
        run_data = {
            "id": etl_run[0],
            "competence": etl_run[1],
            "status": etl_run[2],
            "started_at": etl_run[3].isoformat() if etl_run[3] else None,
            "finished_at": etl_run[4].isoformat() if etl_run[4] else None,
            "files_total": etl_run[5],
            "files_processed": etl_run[6],
            "rows_processed": etl_run[7],
            "error": etl_run[8],
            "cancel_requested_at": etl_run[9].isoformat() if etl_run[9] else None,
        }
    source_data = [
        {
            "source": row[0],
            "completed": row[1],
            "failed": row[2],
            "running": row[3],
            "total": row[4],
        }
        for row in source_rows
    ]
    core_sources = {"receita", "email_quality", "website", "rdap"}
    core_source_data = [item for item in source_data if item["source"] in core_sources]
    core_processed_checks = sum(
        item["completed"] + item["failed"] + item["running"] for item in core_source_data
    )
    latest_source = latest_source_run[0] if latest_source_run else None
    enrichment_data = None
    if latest_enrichment_run:
        enrichment_data = {
            "id": latest_enrichment_run[0],
            "status": latest_enrichment_run[1],
            "started_at": latest_enrichment_run[2].isoformat(),
            "finished_at": (
                latest_enrichment_run[3].isoformat() if latest_enrichment_run[3] else None
            ),
            "processed": latest_enrichment_run[4],
            "done": latest_enrichment_run[5],
            "partial": latest_enrichment_run[6],
            "no_site": latest_enrichment_run[7],
            "failed": latest_enrichment_run[8],
            "activity_at": latest_enrichment_run[9].isoformat(),
        }

    activity_candidates = []
    active_file = next(
        (item for item in files if item["status"] in {"downloading", "processing"}),
        None,
    )
    if active_file:
        if active_file["status"] == "downloading":
            label = f"Baixando {active_file['name']}"
            current = active_file["downloaded_bytes"]
            total = active_file["bytes"]
        else:
            label = (
                f"Lendo {active_file['name']}: "
                f"{int(active_file['scanned_rows'] or 0):,} linhas examinadas, "
                f"{int(active_file['rows'] or 0):,} elegíveis"
            )
            current = active_file["scanned_rows"]
            total = None
        activity_candidates.append(
            {
                "phase": active_file["status"],
                "label": label,
                "current": current,
                "total": total,
                "updated_at": active_file["activity_at"],
            }
        )
    if enrichment_data and enrichment_data["status"] == "running":
        activity_candidates.append(
            {
                "phase": "enrichment",
                "label": (
                    "Enriquecendo presença digital: "
                    f"{int(enrichment_data['processed'] or 0):,} empresas avaliadas"
                ),
                "current": enrichment_data["processed"],
                "total": None,
                "updated_at": enrichment_data["activity_at"],
            }
        )
    if latest_source_run and latest_source_run[1] == "running":
        activity_candidates.append(
            {
                "phase": "intelligence",
                "label": (
                    f"Consultando {latest_source}: "
                    f"{int(latest_source_run[2] or 0):,} empresas verificadas"
                ),
                "current": latest_source_run[2],
                "total": None,
                "updated_at": latest_source_run[8].isoformat(),
            }
        )
    activity = max(
        activity_candidates,
        key=lambda item: item.get("updated_at") or "",
        default=None,
    )
    return {
        "etl_run": run_data,
        "files": files,
        "storage": {
            "database_bytes": database_bytes,
            "limit_bytes": storage_limit_mb * 1024 * 1024 if storage_limit_mb else None,
            "schemas": {row[0]: row[1] for row in schema_rows},
        },
        "counts": {
            "companies": counts[0],
            "enriched": counts[1],
            "qualified": counts[2],
            "rejected": counts[3],
            "rejected_below_score": counts[4],
            "rejected_pre_enrichment": counts[5],
        },
        "enrichment": enrichment_data,
        "activity": activity,
        "intelligence": {
            "profiles": profiles,
            "completed_checks": sum(item["completed"] for item in source_data),
            "failed_checks": sum(item["failed"] for item in source_data),
            "running_checks": sum(item["running"] for item in source_data),
            "core_processed_checks": core_processed_checks,
            "core_total_checks": int(counts[1] or 0) * len(core_sources),
            "current_source": (
                latest_source if latest_source_run and latest_source_run[1] == "running" else None
            ),
            "latest_source": latest_source,
            "latest_run": {
                "status": latest_source_run[1],
                "processed": latest_source_run[2],
                "success": latest_source_run[3],
                "no_data": latest_source_run[4],
                "failed": latest_source_run[5],
                "started_at": latest_source_run[6].isoformat(),
                "finished_at": (
                    latest_source_run[7].isoformat() if latest_source_run[7] else None
                ),
                "activity_at": latest_source_run[8].isoformat(),
            }
            if latest_source_run
            else None,
            "sources": source_data,
        },
    }


@app.get("/")
def root():
    return {
        "service": "cnpj-etl",
        "status": "ok",
        "docs": "/docs",
        "endpoints": [
            "/api/health",
            "/api/db",
            "/api/runs",
            "/api/workflow-runs",
            "/api/workflow-runs/{run_id}",
            "/api/workflow-runs/{run_id}/cancel",
            "/api/enrichment/stats",
            "/api/intelligence/sources",
            "/api/intelligence/companies/{cnpj}",
            "/api/intelligence/feedback",
            "/api/opportunities",
            "/api/opportunities/{cnpj}",
            "/api/opportunities/rebuild",
        ],
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
            # Um valor legado no ambiente não deve reativar silenciosamente a
            # antiga whitelist. A restrição só existe quando o operador a
            # informa explicitamente ao iniciar a carga.
            "cnaes": "",
            "ufs": "",
            "geographic_scope": "Brasil inteiro",
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
            "min_confidence_score": int(os.getenv("PROSPECT_MIN_CONFIDENCE_SCORE", "70")),
            "min_lead_score": int(os.getenv("PROSPECT_MIN_LEAD_SCORE", "70")),
            "load_batch_size": int(os.getenv("LOAD_BATCH_SIZE", "100000")),
            "force_etl": True,
            "continuous": True,
            "force_enrich": False,
            "enrich_batch_size": int(os.getenv("ENRICH_BATCH_SIZE", "2000")),
            "intelligence_sources": os.getenv(
                "INTELLIGENCE_SOURCES",
                "receita,email_quality,website,rdap,cvm,gdelt,pncp,inpi,google_places,pagespeed,meta_ads,google_ads,people_provider",
            ),
            "intelligence_batch_size": int(os.getenv("INTELLIGENCE_BATCH_SIZE", "250")),
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
                "load_batch_size": str(config.load_batch_size),
                "force_etl": str(config.force_etl).lower(),
                "continuous": str(config.continuous).lower(),
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


def _dispatch_auxiliary_workflow(filename: str) -> None:
    response = requests.post(
        f"https://api.github.com/repos/{_github_repo()}/actions/workflows/{filename}/dispatches",
        headers=_github_headers(require_token=True),
        json={"ref": os.getenv("GITHUB_WORKFLOW_REF", "main")},
        timeout=20,
    )
    if response.status_code != 204:
        log.error("GitHub workflow dispatch failed: %s %s", response.status_code, response.text)
        raise HTTPException(status_code=502, detail="Não foi possível iniciar a operação")


@app.post(
    "/api/publish-qualified",
    dependencies=[Depends(_require_write_api_key)],
    status_code=202,
)
def publish_qualified():
    """Publica A/B no Outreach sem interferir no workflow de carga."""
    workflow = os.getenv("GITHUB_PUBLISH_WORKFLOW_FILE", "publish-qualified.yml")
    _dispatch_auxiliary_workflow(workflow)
    return {"started": True, "workflow": workflow}


@app.post(
    "/api/cleanup-storage",
    dependencies=[Depends(_require_write_api_key)],
    status_code=202,
)
def cleanup_storage():
    """Compacta intermediários; o job recusa executar com ETL ativa."""
    workflow = os.getenv("GITHUB_CLEANUP_WORKFLOW_FILE", "cleanup-storage.yml")
    _dispatch_auxiliary_workflow(workflow)
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


@app.get(
    "/api/workflow-runs/{run_id}",
    dependencies=[Depends(_require_api_key)],
)
def workflow_run_detail(run_id: int):
    run, jobs, log_lines = _github_run(run_id)
    steps = [
        {
            "number": step.get("number"),
            "name": step.get("name"),
            "status": step.get("status"),
            "conclusion": step.get("conclusion"),
            "started_at": step.get("started_at"),
            "completed_at": step.get("completed_at"),
        }
        for job in jobs
        for step in job.get("steps", [])
    ]
    try:
        telemetry = _database_telemetry(run_id)
    except Exception:
        log.exception("Database telemetry failed for workflow %s", run_id)
        telemetry = {
            "etl_run": None,
            "files": [],
            "storage": {"database_bytes": 0, "limit_bytes": None, "schemas": {}},
            "counts": {
                "companies": 0,
                "enriched": 0,
                "qualified": 0,
                "rejected": 0,
                "rejected_below_score": 0,
                "rejected_pre_enrichment": 0,
            },
            "warning": "Telemetria do banco temporariamente indisponível",
        }
    log_lines = _runtime_log_lines(run, steps, telemetry, log_lines)
    return {
        "run": {
            "id": run["id"],
            "status": run["status"],
            "conclusion": run.get("conclusion"),
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
            "html_url": run["html_url"],
            "cancel_url": run.get("cancel_url"),
        },
        "progress": _workflow_progress(run["status"], steps),
        "current_step": next(
            (step["name"] for step in steps if step["status"] == "in_progress"),
            "Concluído" if run["status"] == "completed" else "Aguardando executor",
        ),
        "steps": steps,
        "logs": log_lines,
        **telemetry,
    }


@app.post(
    "/api/workflow-runs/{run_id}/cancel",
    dependencies=[Depends(_require_write_api_key)],
    status_code=202,
)
def cancel_workflow_run(run_id: int):
    run, _, _ = _github_run(run_id)
    if run["status"] == "completed":
        raise HTTPException(status_code=409, detail="Esta execução já terminou")
    response = requests.post(
        f"https://api.github.com/repos/{_github_repo()}/actions/runs/{run_id}/cancel",
        headers=_github_headers(require_token=True),
        timeout=20,
    )
    if response.status_code not in {202, 409}:
        log.error("GitHub workflow cancel failed: %s %s", response.status_code, response.text)
        raise HTTPException(status_code=502, detail="Não foi possível abortar a execução")
    try:
        db = Database(Settings().database_url)
        with db.connect() as conn:
            conn.execute(
                """
                UPDATE etl.runs
                SET status='cancelled',finished_at=COALESCE(finished_at,now()),
                    cancel_requested_at=now()
                WHERE workflow_run_id=%s AND status='running'
                """,
                (run_id,),
            )
            conn.commit()
    except Exception:
        log.exception("Could not mark workflow %s as cancelled", run_id)
    return {"cancelled": True, "run_id": run_id}


@app.post(
    "/api/workflow-runs/{run_id}/pause",
    dependencies=[Depends(_require_write_api_key)],
    status_code=202,
)
def pause_workflow_run(run_id: int):
    """Pausa por cancelamento recuperável; o cursor permanece no banco."""
    run, _, _ = _github_run(run_id)
    if run["status"] == "completed":
        raise HTTPException(status_code=409, detail="Esta execução já terminou")
    response = requests.post(
        f"https://api.github.com/repos/{_github_repo()}/actions/runs/{run_id}/cancel",
        headers=_github_headers(require_token=True),
        timeout=20,
    )
    if response.status_code not in {202, 409}:
        raise HTTPException(status_code=502, detail="Não foi possível pausar a execução")
    try:
        database = Database(Settings().database_url)
        with database.connect() as conn:
            conn.execute(
                """
                UPDATE etl.runs
                SET status='paused',finished_at=COALESCE(finished_at,now()),
                    cancel_requested_at=now()
                WHERE workflow_run_id=%s AND status='running'
                """,
                (run_id,),
            )
            conn.commit()
    except Exception:
        log.exception("Could not mark workflow %s as paused", run_id)
    return {"paused": True, "resumable": True, "run_id": run_id}


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


@app.get("/api/opportunities", dependencies=[Depends(_require_api_key)])
def opportunities(
    q: str | None = None,
    state: str | None = None,
    city: str | None = None,
    segment_fit: str | None = None,
    classification: str | None = None,
    min_score: int | None = Query(default=None, ge=0, le=100),
    min_employees: int | None = Query(default=None, ge=0),
    min_units: int | None = Query(default=None, ge=1),
    has_whatsapp: bool | None = None,
    has_crm: bool | None = None,
    has_erp: bool | None = None,
    has_ecommerce: bool | None = None,
    has_sales_team: bool | None = None,
    technology: str | None = None,
    signal_type: str | None = None,
    signal_since_days: int | None = Query(default=None, ge=0, le=3650),
    preset: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
):
    filters = {key: value for key, value in locals().items() if value is not None}
    try:
        db = Database(Settings().database_url)
        with db.connect() as conn:
            return list_opportunities(conn, filters)
    except Exception:
        log.exception("Opportunity search failed")
        raise _service_error() from None


@app.get("/api/opportunities/{cnpj}", dependencies=[Depends(_require_api_key)])
def opportunity_detail(cnpj: str):
    digits = re.sub(r"\D", "", cnpj)
    if len(digits) != 14:
        raise HTTPException(status_code=422, detail="CNPJ inválido")
    try:
        db = Database(Settings().database_url)
        with db.connect() as conn:
            result = get_opportunity(conn, digits)
    except Exception:
        log.exception("Opportunity detail failed")
        raise _service_error() from None
    if not result:
        raise HTTPException(status_code=404, detail="Oportunidade não encontrada")
    return result


@app.post("/api/opportunities/rebuild", dependencies=[Depends(_require_write_api_key)])
def opportunity_rebuild(limit: int = Query(default=1000, ge=1, le=10000)):
    try:
        db = Database(Settings().database_url)
        with db.connect() as conn:
            total = rebuild_profiles(conn, limit)
    except Exception:
        log.exception("Opportunity rebuild failed")
        raise _service_error() from None
    return {"processed": total}


@app.post("/api/intelligence/feedback", dependencies=[Depends(_require_write_api_key)])
def intelligence_feedback(payload: CommercialFeedback):
    try:
        db = Database(Settings().database_url)
        with db.connect() as conn:
            result = record_feedback(conn, payload.model_dump())
    except Exception:
        log.exception("Commercial feedback failed")
        raise _service_error() from None
    return result
