"""Orquestrador de enriquecimento digital v2 (compatível com imports legados)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from psycopg import sql
from psycopg.types.json import Jsonb

from .enrichment.commerce import (
    analyze_page_html,
    crawl_transactional_pages,
    extract_instagram_link,
    extract_linkedin_link,
    has_strong_commerce_from_flags,
)
from .enrichment.email import classify_email
from .enrichment.google_places import enrich_from_google_places
from .enrichment.models import (
    ALLOWED_CANDIDATE_VIEWS,
    SCORE_VERSION,
    EnrichResult,
    EnrichSettings,
)
from .enrichment.providers.speedio import SpeedioProvider
from .enrichment.scoring import apply_all_scores, classify_company_size_band

# Re-exports legados para testes
from .enrichment.scoring import calculate_score, estimate_revenue_band  # noqa: F401
from .enrichment.commerce import detect_platforms  # noqa: F401
from .enrichment.whatsapp import extract_whatsapp_candidate  # noqa: F401
from .enrichment.website import (
    http_session,
    safe_fetch,
    site_candidates_from_email,
    validate_site_ownership,
)
from .enrichment.whatsapp import (
    extract_whatsapp_from_html,
    phone_to_whatsapp_candidate,
)

log = logging.getLogger(__name__)

ADMIN_QUALIFICATIONS = frozenset({"05", "16", "17", "49"})
ENRICHMENT_ADVISORY_LOCK = 7262603882


def extract_social_links(html: str, base_url: str | None) -> dict[str, str | None]:
    wa = extract_whatsapp_from_html(html, base_url)
    return {
        "instagram": extract_instagram_link(html, base_url),
        "whatsapp": wa.get("canonical_url") if wa.get("valid") else None,
        "linkedin": extract_linkedin_link(html, base_url),
    }


def resolve_candidate_view(name: str) -> str:
    if name not in ALLOWED_CANDIDATE_VIEWS:
        raise ValueError(f"View não autorizada: {name}")
    return name


def acquire_enrichment_lock(conn) -> bool:
    return conn.execute("SELECT pg_try_advisory_lock(%s)", (ENRICHMENT_ADVISORY_LOCK,)).fetchone()[
        0
    ]


def release_enrichment_lock(conn) -> None:
    conn.execute("SELECT pg_advisory_unlock(%s)", (ENRICHMENT_ADVISORY_LOCK,))


def fetch_decisor(conn, cnpj_basico: str) -> tuple[str | None, str | None]:
    row = conn.execute(
        """
        SELECT nome_socio_razao_social, qualificacao_socio
        FROM cnpj.socios
        WHERE cnpj_basico = %s
        ORDER BY
          CASE WHEN qualificacao_socio = ANY(%s) THEN 0 ELSE 1 END,
          data_entrada_sociedade NULLS LAST
        LIMIT 1
        """,
        (cnpj_basico, list(ADMIN_QUALIFICATIONS)),
    ).fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def count_domain_shared(conn, domain: str | None, exclude_cnpj: str | None = None) -> int:
    if not domain:
        return 0
    if exclude_cnpj:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT cnpj)
            FROM cnpj.digital_presenca
            WHERE email_dominio = %s AND cnpj <> %s
            """,
            (domain, exclude_cnpj),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT cnpj)
            FROM cnpj.digital_presenca
            WHERE email_dominio = %s
            """,
            (domain,),
        ).fetchone()
    return int(row[0] or 0)


def _compute_next_retry(result: EnrichResult, settings: EnrichSettings) -> datetime | None:
    now = datetime.now(timezone.utc)
    if result.retry_reason == "google_places_no_result":
        return now + timedelta(days=90)
    fetch = result.sinais.get("fetch_meta") or {}
    if fetch.get("retry_after_days"):
        return now + timedelta(days=int(fetch["retry_after_days"]))
    if fetch.get("retry_after_hours"):
        return now + timedelta(hours=int(fetch["retry_after_hours"]))
    if result.enrich_status == "no_site":
        return now + timedelta(days=settings.no_site_retry_days)
    if result.enrich_status == "failed":
        return now + timedelta(hours=settings.failed_retry_hours)
    return None


def enrich_record(row: dict, conn, settings: EnrichSettings) -> EnrichResult:
    result = EnrichResult(
        cnpj=row["cnpj"],
        cnpj_basico=row["cnpj_basico"],
        razao_social=row.get("razao_social"),
        nome_fantasia=row.get("nome_fantasia"),
        uf=row.get("uf"),
        municipio_descricao=row.get("municipio_descricao"),
        telefone_1=row.get("telefone_1"),
        logradouro=row.get("logradouro"),
        cep=row.get("cep"),
        cnae_fiscal_principal=row.get("cnae_fiscal_principal"),
    )
    result.email_original = row.get("email")
    domain, normalized_email, email_tipo = classify_email(result.email_original)
    result.email_dominio = domain
    result.email_tipo = email_tipo
    result.sinais["email"] = normalized_email
    result.sinais["porte"] = row.get("porte")
    result.sinais["opcao_mei"] = row.get("opcao_mei")

    faixa, fonte = classify_company_size_band(
        porte=row.get("porte"),
        opcao_mei=row.get("opcao_mei"),
        opcao_simples=row.get("opcao_simples"),
        capital_social=row.get("capital_social"),
    )
    result.faixa_porte_receita = faixa
    result.porte_receita_fonte = fonte
    result.faixa_faturamento_estimada = faixa
    result.faturamento_fonte = fonte

    decisor, qualificacao = fetch_decisor(conn, result.cnpj_basico)
    result.decisor_nome = decisor
    result.decisor_qualificacao = qualificacao

    if settings.external_api_provider == "speedio":
        SpeedioProvider().enrich(result.cnpj, result, settings)

    if settings.brasilapi_enabled:
        try:
            response = requests.get(
                f"https://brasilapi.com.br/api/cnpj/v1/{result.cnpj}",
                timeout=settings.request_timeout,
            )
            if response.status_code == 200:
                payload = response.json()
                result.sinais["brasilapi_situacao"] = payload.get("descricao_situacao_cadastral")
        except requests.RequestException as exc:
            result.sinais["brasilapi_error"] = exc.__class__.__name__

    cached = row.get("google_places_checked_at")
    enrich_from_google_places(
        result,
        settings,
        cached_checked_at=cached,
        cached_place_id=row.get("google_place_id"),
    )

    shared = count_domain_shared(conn, domain, result.cnpj)
    result.domain_shared_count = shared
    result.domain_is_shared = shared >= 3

    session = http_session()
    candidates: list[tuple[str, str]] = []
    if result.site_url:
        candidates.append((result.site_url, result.site_source or "external_provider"))
    for url in site_candidates_from_email(domain, email_tipo):
        candidates.append((url, "corporate_email_domain"))

    last_error = None
    html = ""
    for url, source in candidates:
        fetch = safe_fetch(session, url, settings)
        if fetch.error:
            last_error = fetch.error
            result.sinais.setdefault("site_probe_errors", []).append({url: fetch.error})
            result.sinais["fetch_meta"] = {
                "retry_after_days": fetch.retry_after_days,
                "retry_after_hours": fetch.retry_after_hours,
            }
        if fetch.status is None:
            continue

        result.site_http_status = fetch.status
        result.site_final_url = fetch.final_url
        result.site_content_type = fetch.content_type
        result.site_redirect_count = fetch.redirect_count
        result.site_reachable = fetch.reachable
        result.site_url = fetch.final_url or url
        result.site_source = source
        result.site_titulo = fetch.title[:250] if fetch.title else None
        result.site_ativo = fetch.reachable and fetch.status not in (404, 410)
        html = fetch.html

        if fetch.reachable and html:
            match_score, reasons, status = validate_site_ownership(
                html=html,
                title=fetch.title,
                final_url=fetch.final_url,
                nome_fantasia=result.nome_fantasia,
                razao_social=result.razao_social,
                municipio=result.municipio_descricao,
                uf=result.uf,
                telefone=result.telefone_1,
                cnpj=result.cnpj,
                email_domain=domain,
                email_tipo=email_tipo,
                cnae=result.cnae_fiscal_principal,
                domain_shared_count=shared,
            )
            result.site_match_score = match_score
            result.site_validation_reasons = reasons
            result.site_match_status = status
            result.site_valid = (
                match_score >= settings.site_match_threshold and status == "validated"
            )
            if "cnpj_no_site" in reasons:
                result.sinais["cnpj_on_site"] = True
            if status == "rejected_webmail":
                result.site_valid = False
                break
            if result.site_valid or fetch.status in (401, 403):
                break
        elif fetch.status in (404, 410):
            result.site_valid = False
            break

    if html and result.site_reachable:
        page_signals = analyze_page_html(html, result.site_final_url)
        if page_signals.coming_soon:
            result.transactional_signals["coming_soon"] = True
        result.has_product_page = page_signals.has_product_page
        result.has_product_schema = page_signals.has_product_schema
        result.has_price = page_signals.has_price
        result.has_cart = page_signals.has_cart
        result.has_checkout = page_signals.has_checkout
        result.has_add_to_cart = page_signals.has_add_to_cart
        result.has_catalog = page_signals.has_catalog
        result.has_search = page_signals.has_search
        result.has_customer_login = page_signals.has_customer_login
        result.has_chat = page_signals.has_chat
        result.chat_provider = page_signals.chat_provider
        result.has_contact_form = page_signals.has_contact_form
        result.plataforma = page_signals.plataforma
        result.plataformas_detectadas = page_signals.plataformas_detectadas
        result.plataforma_confianca = page_signals.plataforma_confianca

        if result.site_valid:
            crawled = crawl_transactional_pages(
                session, result.site_final_url or result.site_url, settings
            )
            result.has_product_page = result.has_product_page or crawled.has_product_page
            result.has_product_schema = result.has_product_schema or crawled.has_product_schema
            result.has_price = result.has_price or crawled.has_price
            result.has_cart = result.has_cart or crawled.has_cart
            result.has_checkout = result.has_checkout or crawled.has_checkout
            result.has_add_to_cart = result.has_add_to_cart or crawled.has_add_to_cart
            result.has_catalog = result.has_catalog or crawled.has_catalog
            result.has_search = result.has_search or crawled.has_search
            result.has_chat = result.has_chat or crawled.has_chat
            result.chat_provider = result.chat_provider or crawled.chat_provider
            if crawled.plataforma and crawled.plataforma_confianca > result.plataforma_confianca:
                result.plataforma = crawled.plataforma
                result.plataforma_confianca = crawled.plataforma_confianca
                result.plataformas_detectadas = crawled.plataformas_detectadas

        wa = extract_whatsapp_from_html(html, result.site_final_url)
        if wa["detected"]:
            result.whatsapp_detected = True
            result.whatsapp_source = "website"
            result.whatsapp_confidence = wa["confidence"]
            if wa["valid"]:
                result.whatsapp_valid = True
                result.whatsapp_number = wa["number"]
                result.whatsapp_number_normalized = wa["normalized"]
                result.whatsapp_url = wa["canonical_url"]
            else:
                result.whatsapp_valid = False

        result.instagram_url = extract_instagram_link(html, result.site_final_url)
        result.linkedin_url = extract_linkedin_link(html, result.site_final_url)

        if result.site_valid and has_strong_commerce_from_flags(
            has_checkout=result.has_checkout,
            has_cart=result.has_cart,
            has_add_to_cart=result.has_add_to_cart,
            has_product_schema=result.has_product_schema,
            has_price=result.has_price,
            plataforma=result.plataforma,
        ):
            result.enrich_status = "done"
        elif result.site_reachable:
            result.enrich_status = "partial"
        else:
            result.enrich_status = "failed"
    elif not result.site_url:
        result.enrich_status = "no_site"
    elif not result.site_reachable:
        result.enrich_status = "failed"
        result.enrich_error = last_error
    else:
        result.enrich_status = "partial"

    if result.telefone_1 and not result.whatsapp_valid:
        candidate = phone_to_whatsapp_candidate(result.telefone_1)
        if candidate:
            result.telefone_candidato_whatsapp = candidate
            result.sinais["telefone_candidato"] = True

    apply_all_scores(result)
    result.enrichment_version = settings.enrichment_version
    result.enrich_attempts = int(row.get("enrich_attempts") or 0) + 1
    next_retry = _compute_next_retry(result, settings)
    result._next_retry_at = next_retry  # noqa: SLF001 — uso interno upsert
    return result


def rescore_from_row(row: dict) -> EnrichResult:
    """Recalcula scores v2 a partir de dados persistidos, sem HTTP."""
    result = EnrichResult(cnpj=row["cnpj"], cnpj_basico=row["cnpj_basico"])
    for key, value in row.items():
        if hasattr(result, key) and key not in {"cnpj", "cnpj_basico"}:
            setattr(result, key, value)
    if isinstance(result.sinais, str):
        result.sinais = {}
    apply_all_scores(result)
    result.score_version = SCORE_VERSION
    result.digital_score = result.lead_score
    result.digital_maturity = result.commerce_maturity
    return result


_UPSERT_COLUMNS = [
    "cnpj",
    "cnpj_basico",
    "email_original",
    "email_dominio",
    "email_tipo",
    "site_url",
    "site_ativo",
    "site_http_status",
    "site_titulo",
    "site_source",
    "site_match_score",
    "site_match_status",
    "site_reachable",
    "site_valid",
    "site_content_type",
    "site_final_url",
    "site_redirect_count",
    "site_validation_reasons",
    "domain_shared_count",
    "domain_is_shared",
    "plataforma",
    "plataformas_detectadas",
    "plataforma_confianca",
    "instagram_url",
    "whatsapp_url",
    "linkedin_url",
    "whatsapp_detected",
    "whatsapp_number",
    "whatsapp_number_normalized",
    "whatsapp_valid",
    "whatsapp_source",
    "whatsapp_confidence",
    "telefone_candidato_whatsapp",
    "google_place_id",
    "google_place_match_score",
    "google_place_name",
    "google_place_address",
    "google_place_phone",
    "google_place_website",
    "google_business_status",
    "google_rating",
    "google_rating_count",
    "google_maps_url",
    "has_product_page",
    "has_product_schema",
    "has_price",
    "has_cart",
    "has_checkout",
    "has_add_to_cart",
    "has_catalog",
    "has_search",
    "has_customer_login",
    "has_chat",
    "chat_provider",
    "has_contact_form",
    "transactional_signals",
    "decisor_nome",
    "decisor_qualificacao",
    "faixa_faturamento_estimada",
    "faturamento_fonte",
    "faixa_porte_receita",
    "porte_receita_fonte",
    "faturamento_estimado",
    "faturamento_estimado_fonte",
    "presence_score",
    "commerce_score",
    "fit_score",
    "pain_score",
    "confidence_score",
    "lead_score",
    "presence_maturity",
    "commerce_maturity",
    "lead_classification",
    "score_version",
    "digital_score",
    "digital_maturity",
    "sinais",
    "enrich_status",
    "enrich_error",
    "enrich_attempts",
    "retry_reason",
    "enrichment_version",
]


def _result_to_params(result: EnrichResult) -> dict[str, Any]:
    params = {col: getattr(result, col, None) for col in _UPSERT_COLUMNS}
    params["sinais"] = Jsonb(result.sinais)
    params["transactional_signals"] = Jsonb(result.transactional_signals)
    params["site_last_checked_at"] = datetime.now(timezone.utc)
    params["last_attempt_at"] = datetime.now(timezone.utc)
    params["next_retry_at"] = getattr(result, "_next_retry_at", None)
    params["google_places_checked_at"] = (
        datetime.now(timezone.utc) if result.google_place_id else None
    )
    return params


def upsert_result(conn, result: EnrichResult) -> None:
    params = _result_to_params(result)
    timing = [
        "site_last_checked_at",
        "last_attempt_at",
        "next_retry_at",
        "google_places_checked_at",
    ]
    all_cols = _UPSERT_COLUMNS + timing
    placeholders = [f"%({k})s" for k in all_cols] + ["now()", "now()"]
    insert_cols = all_cols + ["enriched_at", "updated_at"]
    updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in all_cols if col != "cnpj")
    conn.execute(
        f"""
        INSERT INTO cnpj.digital_presenca ({", ".join(insert_cols)})
        VALUES ({", ".join(placeholders)})
        ON CONFLICT (cnpj) DO UPDATE SET {updates},
            enriched_at = now(),
            updated_at = now(),
            processing_run_id = NULL,
            processing_started_at = NULL
        """,
        params,
    )


def fetch_pending(
    conn,
    limit: int,
    *,
    force: bool = False,
    candidate_view: str = "cnpj.v_prospect_candidates",
    exclude_cnpjs: set[str] | None = None,
    after_cnpj: str | None = None,
    enrichment_version: str = "v2",
) -> list[dict]:
    view = resolve_candidate_view(candidate_view)
    exclude_cnpjs = exclude_cnpjs or set()
    params: list[Any] = [enrichment_version]
    conditions = [
        """
        (
          d.cnpj IS NULL
          OR d.enrichment_version IS DISTINCT FROM %s
          OR d.enrich_status = 'pending'
          OR (d.enrich_status = 'failed' AND (d.next_retry_at IS NULL OR d.next_retry_at <= now()))
          OR (d.enrich_status = 'no_site' AND (d.next_retry_at IS NULL OR d.next_retry_at <= now()))
        )
        """,
        "(d.processing_run_id IS NULL OR d.processing_started_at < now() - interval '2 hours')",
    ]
    if force:
        conditions = ["TRUE"]
    if exclude_cnpjs:
        conditions.append("v.cnpj <> ALL(%s)")
        params.append(list(exclude_cnpjs))
    if after_cnpj:
        conditions.append("v.cnpj > %s")
        params.append(after_cnpj)
    params.append(limit)

    query = sql.SQL(
        """
        SELECT
          v.cnpj, v.cnpj_basico, v.email, v.telefone_1, v.nome_fantasia,
          v.razao_social, v.uf, v.municipio_descricao, v.logradouro, v.cep,
          v.cnae_fiscal_principal, v.porte, v.opcao_mei, v.opcao_simples, v.capital_social,
          d.enrich_attempts, d.google_place_id, d.google_places_checked_at
        FROM {view} v
        LEFT JOIN cnpj.digital_presenca d ON d.cnpj = v.cnpj
        WHERE {where_clause}
        ORDER BY v.cnpj ASC
        LIMIT %s
        """
    ).format(
        view=sql.Identifier(*view.split(".")),
        where_clause=sql.SQL(" AND ").join(sql.SQL(c) for c in conditions),
    )
    rows = conn.execute(query, params).fetchall()
    columns = [
        "cnpj",
        "cnpj_basico",
        "email",
        "telefone_1",
        "nome_fantasia",
        "razao_social",
        "uf",
        "municipio_descricao",
        "logradouro",
        "cep",
        "cnae_fiscal_principal",
        "porte",
        "opcao_mei",
        "opcao_simples",
        "capital_social",
        "enrich_attempts",
        "google_place_id",
        "google_places_checked_at",
    ]
    return [dict(zip(columns, row)) for row in rows]


def ensure_digital_presenca_schema(conn) -> None:
    row = conn.execute(
        """
        SELECT COUNT(*) >= 1
        FROM information_schema.columns
        WHERE table_schema = 'cnpj'
          AND table_name = 'digital_presenca'
          AND column_name = 'lead_score'
        """
    ).fetchone()[0]
    if not row:
        raise RuntimeError("Schema v2 incompleto. Rode: python -m cnpj_etl.cli migrate")


def run_enrichment(
    conn,
    settings: EnrichSettings | None = None,
    *,
    force: bool = False,
    exclude_cnpjs: set[str] | None = None,
    after_cnpj: str | None = None,
) -> dict[str, int]:
    settings = settings or EnrichSettings()
    ensure_digital_presenca_schema(conn)
    if not acquire_enrichment_lock(conn):
        log.warning("Outro processo de enriquecimento está ativo")
        return {"processed": 0, "done": 0, "partial": 0, "no_site": 0, "failed": 0, "locked": 1}

    run_id = conn.execute(
        """
        INSERT INTO etl.enrichment_runs (enrichment_version, status)
        VALUES (%s, 'running') RETURNING id
        """,
        (settings.enrichment_version,),
    ).fetchone()[0]
    conn.commit()

    stats = {"processed": 0, "done": 0, "partial": 0, "no_site": 0, "failed": 0}
    if exclude_cnpjs is None:
        processed_cnpjs: set[str] = set()
    else:
        processed_cnpjs = exclude_cnpjs
    rows: list[dict] = []

    try:
        rows = fetch_pending(
            conn,
            settings.batch_size,
            force=force,
            candidate_view=settings.candidate_view,
            exclude_cnpjs=processed_cnpjs,
            after_cnpj=after_cnpj,
            enrichment_version=settings.enrichment_version,
        )
        log.info("Enriquecimento digital: %s registros na fila", len(rows))
        for row in rows:
            cnpj = row["cnpj"]
            if cnpj in processed_cnpjs:
                continue
            processed_cnpjs.add(cnpj)
            try:
                result = enrich_record(row, conn, settings)
                upsert_result(conn, result)
                conn.commit()
                stats["processed"] += 1
                status_key = result.enrich_status if result.enrich_status in stats else "partial"
                stats[status_key] = stats.get(status_key, 0) + 1
            except Exception as exc:
                conn.rollback()
                stats["failed"] += 1
                log.exception("Falha ao enriquecer %s: %s", cnpj, exc)
            import time

            time.sleep(settings.delay_seconds)
    finally:
        conn.execute(
            """
            UPDATE etl.enrichment_runs
            SET finished_at = now(), status = 'done',
                processed = %s, done = %s, partial = %s, no_site = %s, failed = %s
            WHERE id = %s
            """,
            (
                stats["processed"],
                stats.get("done", 0),
                stats.get("partial", 0),
                stats.get("no_site", 0),
                stats.get("failed", 0),
                run_id,
            ),
        )
        conn.commit()
        release_enrichment_lock(conn)

    stats["processed_cnpjs"] = len(processed_cnpjs)
    if rows:
        stats["last_cnpj"] = rows[-1]["cnpj"]
    return stats


def run_enrichment_until_empty(
    conn,
    settings: EnrichSettings | None = None,
    *,
    force: bool = False,
) -> dict[str, int]:
    settings = settings or EnrichSettings()
    totals = {"processed": 0, "done": 0, "partial": 0, "no_site": 0, "failed": 0, "rounds": 0}
    processed: set[str] = set()
    after_cnpj: str | None = None

    for _ in range(settings.max_rounds):
        stats = run_enrichment(
            conn,
            settings,
            force=force,
            exclude_cnpjs=processed,
            after_cnpj=after_cnpj if force else None,
        )
        totals["rounds"] += 1
        for key in ("processed", "done", "partial", "no_site", "failed"):
            totals[key] += stats.get(key, 0)
        if stats.get("locked"):
            break
        batch_count = stats.get("processed", 0)
        if batch_count == 0:
            break
        if force and stats.get("last_cnpj"):
            after_cnpj = stats["last_cnpj"]
    log.info("Enriquecimento finalizado após %s rodadas: %s", totals["rounds"], totals)
    return totals


def rescore_all(conn, *, version: str = "v2", batch_size: int = 500) -> dict[str, int]:
    columns = [
        "cnpj",
        "cnpj_basico",
        "email_tipo",
        "site_valid",
        "site_reachable",
        "site_match_status",
        "site_validation_reasons",
        "instagram_url",
        "linkedin_url",
        "whatsapp_valid",
        "whatsapp_detected",
        "whatsapp_confidence",
        "google_place_match_score",
        "google_business_status",
        "plataforma",
        "plataforma_confianca",
        "has_product_page",
        "has_product_schema",
        "has_price",
        "has_cart",
        "has_checkout",
        "has_add_to_cart",
        "has_catalog",
        "has_search",
        "has_customer_login",
        "has_chat",
        "chat_provider",
        "has_contact_form",
        "transactional_signals",
        "domain_is_shared",
        "sinais",
        "cnae_fiscal_principal",
        "commerce_maturity",
    ]
    total = 0
    while True:
        rows = conn.execute(
            f"""
            SELECT {", ".join(f"v.{c}" if c == "cnae_fiscal_principal" else f"d.{c}" for c in columns)}
            FROM cnpj.digital_presenca d
            LEFT JOIN cnpj.v_prospect_candidates v ON v.cnpj = d.cnpj
            WHERE d.score_version IS DISTINCT FROM %s
            ORDER BY d.cnpj
            LIMIT %s
            """,
            (version, batch_size),
        ).fetchall()
        if not rows:
            break
        for raw in rows:
            item = dict(zip(columns, raw))
            result = rescore_from_row(item)
            conn.execute(
                """
                UPDATE cnpj.digital_presenca SET
                  presence_score=%s, commerce_score=%s, fit_score=%s, pain_score=%s,
                  confidence_score=%s, lead_score=%s,
                  presence_maturity=%s, commerce_maturity=%s, lead_classification=%s,
                  digital_score=%s, digital_maturity=%s, score_version=%s, updated_at=now()
                WHERE cnpj=%s
                """,
                (
                    result.presence_score,
                    result.commerce_score,
                    result.fit_score,
                    result.pain_score,
                    result.confidence_score,
                    result.lead_score,
                    result.presence_maturity,
                    result.commerce_maturity,
                    result.lead_classification,
                    result.digital_score,
                    result.digital_maturity,
                    result.score_version,
                    result.cnpj,
                ),
            )
            total += 1
        conn.commit()
    return {"rescored": total}


def requeue_enrichment(conn, *, reason: str = "version_upgrade", target_version: str = "v2") -> int:
    result = conn.execute(
        """
        UPDATE cnpj.digital_presenca
        SET next_retry_at = now(),
            retry_reason = %s,
            enrich_status = CASE
              WHEN enrich_status IN ('done', 'partial') THEN 'pending'
              ELSE enrich_status
            END,
            enrichment_version = NULL
        WHERE enrichment_version IS DISTINCT FROM %s
           OR score_version IS DISTINCT FROM %s
        """,
        (reason, target_version, target_version),
    )
    conn.commit()
    return result.rowcount
