import logging
from dataclasses import replace
import os
from collections.abc import Callable

from .filters import FILE_LOAD_ORDER, FilterContext, all_company_basics_resolved
from .ibge_population import ensure_municipios_populacao, load_allowed_municipios
from .loader import load_zip
from .retention import prune_evaluated_candidates
from .source import fmt_bytes

log = logging.getLogger(__name__)


def _fmt_remote_size(size: int | None) -> str:
    return fmt_bytes(size) if size else "tamanho desconhecido"


def prepare_run_settings(settings, db, auto_bootstrap: bool = False):
    if settings.filters_enabled():
        settings = replace(settings, include_types=settings.resolved_file_types())
    if not auto_bootstrap:
        return settings
    with db.connect() as conn:
        if db.needs_initial_load(conn):
            if settings.filters_enabled():
                uf_msg = (
                    f", UFs={','.join(sorted(settings.filter_ufs))}" if settings.filter_ufs else ""
                )
                if settings.filter_cnaes:
                    cnae_mode = (
                        "principal+secundário"
                        if settings.filter_include_secondary_cnae
                        else "somente principal"
                    )
                    log.info(
                        "Base vazia — carga filtrada (%s CNAEs %s, somente ativas%s)",
                        len(settings.filter_cnaes),
                        cnae_mode,
                        uf_msg,
                    )
                    log.info("CNAEs: %s", ", ".join(sorted(settings.filter_cnaes)))
                else:
                    log.info(
                        "Base vazia — carga de qualidade em todos os CNAEs (somente ativas%s)",
                        uf_msg,
                    )
                extras = []
                if settings.filter_require_nome_fantasia:
                    extras.append("nome fantasia obrigatório")
                if settings.filter_require_telefone:
                    extras.append("telefone válido obrigatório")
                if settings.filter_require_email:
                    extras.append("e-mail válido obrigatório")
                if settings.filter_block_backoffice_email:
                    extras.append("e-mails contábeis/fiscais bloqueados")
                if settings.filter_headquarters_only:
                    extras.append("somente matrizes")
                if settings.filter_max_candidates_per_run > 0:
                    extras.append(f"até {settings.filter_max_candidates_per_run} candidatos novos")
                if settings.filter_min_activity_months > 0:
                    extras.append(f"atividade >= {settings.filter_min_activity_months} meses")
                if settings.filter_min_population > 0:
                    extras.append(
                        f"municípios >= {settings.filter_min_population:,} hab".replace(",", ".")
                    )
                if extras:
                    log.info("Prospect: %s", ", ".join(extras))
            else:
                log.error(
                    "Filtros desabilitados — carga COMPLETA nacional. "
                    "Defina FILTER_CNAES ou remova DISABLE_FILTERS."
                )
                settings = replace(settings, include_types=frozenset())
            return settings
    log.info("Base já populada — sincronização incremental (apenas arquivos novos ou alterados)")
    return settings


def build_filter_context(settings, conn):
    if not settings.filters_enabled():
        return None
    from .filters import FilterContext

    allowed_municipios = frozenset()
    excluded_cnpjs = frozenset(
        row[0]
        for row in conn.execute(
            "SELECT cnpj FROM etl.candidate_decisions WHERE next_review_at > now()"
        ).fetchall()
    )
    if settings.filter_min_population > 0:
        allowed_municipios = load_allowed_municipios(conn, settings.filter_min_population)
        if not allowed_municipios:
            raise RuntimeError(
                "Tabela cnpj.municipios_populacao vazia. Rode migrate/sync-ibge antes do ETL."
            )
        log.info(
            "Municípios elegíveis (>= %s hab): %s",
            settings.filter_min_population,
            len(allowed_municipios),
        )

    requested_limit = settings.filter_max_candidates_per_run
    storage_limit = max(0, settings.raw_staging_max_rows)
    effective_limit = requested_limit
    if storage_limit and (requested_limit <= 0 or requested_limit > storage_limit):
        effective_limit = storage_limit
        log.info(
            "[ARMAZENAMENTO] lote bruto limitado a %s candidatos (solicitado=%s)",
            storage_limit,
            requested_limit or "sem limite",
        )

    return FilterContext(
        cnaes=settings.filter_cnaes,
        active_only=settings.filter_active_only,
        ufs=settings.filter_ufs,
        include_secondary_cnae=settings.filter_include_secondary_cnae,
        require_nome_fantasia=settings.filter_require_nome_fantasia,
        require_telefone=settings.filter_require_telefone,
        require_email=settings.filter_require_email,
        block_backoffice_email=settings.filter_block_backoffice_email,
        min_activity_months=settings.filter_min_activity_months,
        min_population=settings.filter_min_population,
        headquarters_only=settings.filter_headquarters_only,
        max_candidates=effective_limit,
        allowed_municipios=allowed_municipios,
        excluded_cnpjs=excluded_cnpjs,
    )


def sort_files(files, filter_ctx: FilterContext):
    if not filter_ctx.enabled:
        return files
    return sorted(files, key=lambda f: (FILE_LOAD_ORDER.get(f.file_type, 99), f.name))


def candidate_limit_reached(filter_ctx: FilterContext | None) -> bool:
    return bool(
        filter_ctx
        and filter_ctx.max_candidates > 0
        and len(filter_ctx.selected_cnpjs) >= filter_ctx.max_candidates
    )


def seed_staged_candidates(conn, filter_ctx: FilterContext | None) -> int:
    """Consome primeiro o staging deixado por execuções interrompidas."""
    if not filter_ctx or not filter_ctx.enabled or filter_ctx.max_candidates <= 0:
        return 0
    remaining = filter_ctx.max_candidates - len(filter_ctx.selected_cnpjs)
    if remaining <= 0:
        return 0
    rows = conn.execute(
        """
        SELECT e.cnpj,e.cnpj_basico
        FROM cnpj.estabelecimentos e
        WHERE NOT EXISTS (
          SELECT 1 FROM cnpj.prospectos_qualificados p WHERE p.cnpj=e.cnpj
        )
          AND NOT EXISTS (
            SELECT 1 FROM etl.candidate_decisions d WHERE d.cnpj=e.cnpj
          )
        ORDER BY e.cnpj
        LIMIT %s
        """,
        (remaining,),
    ).fetchall()
    for cnpj, cnpj_basico in rows:
        filter_ctx.selected_cnpjs.add(cnpj)
        filter_ctx.matched_basics.add(cnpj_basico)
    if rows:
        log.info(
            "[ARMAZENAMENTO] retomando %s candidatos brutos já armazenados; "
            "nenhum novo estabelecimento será carregado antes de consumi-los",
            len(rows),
        )
    return len(rows)


def discard_unresolved_staging(conn, filter_ctx: FilterContext | None) -> int:
    """Descarta o lote sem cadastro de empresa após varrer todos os ZIPs."""
    if not filter_ctx:
        return 0
    unresolved = sorted(filter_ctx.matched_basics - filter_ctx.resolved_company_basics)
    if not unresolved:
        return 0
    removed = conn.execute(
        "DELETE FROM cnpj.estabelecimentos WHERE cnpj_basico=ANY(%s)",
        (unresolved,),
    ).rowcount
    for table in ("socios", "simples", "empresas"):
        conn.execute(
            f"DELETE FROM cnpj.{table} WHERE cnpj_basico=ANY(%s)",
            (unresolved,),
        )
    conn.execute(
        """
        UPDATE etl.funnel_metrics
        SET rejected=rejected+%s,updated_at=now()
        WHERE singleton=true
        """,
        (max(0, removed),),
    )
    conn.commit()
    log.info(
        "[ARMAZENAMENTO] descartados %s registros brutos sem cadastro complementar",
        max(0, removed),
    )
    return max(0, removed)


def run(
    settings,
    db,
    source,
    competence: str | None = None,
    force: bool = False,
    auto_bootstrap: bool = False,
    after_file: Callable[[object, object, int], None] | None = None,
):
    if os.getenv("GITHUB_ACTIONS") == "true" and not settings.filters_enabled():
        raise RuntimeError(
            "Filtros CNAE obrigatórios no GitHub Actions. "
            "Configure FILTER_CNAES ou remova DISABLE_FILTERS."
        )
    settings = prepare_run_settings(settings, db, auto_bootstrap)
    with db.connect() as prep_conn:
        if settings.filter_min_population > 0:
            ensure_municipios_populacao(prep_conn, year=settings.ibge_population_year)
            prep_conn.commit()
        filter_ctx = build_filter_context(settings, prep_conn)
    competence = competence or source.latest_competence()
    if force:
        log.info(
            "Modo force — reprocessa ZIPs com filtros atuais (upsert: novos entram, "
            "existentes permanecem; base NÃO é apagada)"
        )
    all_files = source.list_files(competence)
    allowed = settings.resolved_file_types()
    files = [f for f in all_files if not allowed or f.file_type in allowed]
    files = sort_files(
        files,
        filter_ctx or FilterContext(frozenset(), apply_filters=False),
    )
    if settings.keep_downloads:
        target = settings.data_dir / competence
        target.mkdir(parents=True, exist_ok=True)

    with db.connect() as lock_conn:
        if not db.acquire_lock(lock_conn):
            log.warning("Outra execução está ativa; encerrando.")
            return 0
        # Finaliza resíduos de ciclos anteriores e usa o backlog bruto antes de
        # abrir espaço para novos estabelecimentos da Receita.
        prune_evaluated_candidates(lock_conn)
        staged_backlog = seed_staged_candidates(lock_conn, filter_ctx)
        workflow_run_id = os.getenv("GITHUB_RUN_ID")
        run_id = lock_conn.execute(
            "INSERT INTO etl.runs (competence,status,files_total,workflow_run_id) "
            "VALUES (%s,'running',%s,%s) RETURNING id",
            (competence, len(files), int(workflow_run_id) if workflow_run_id else None),
        ).fetchone()[0]
        lock_conn.execute(
            "UPDATE etl.files SET last_run_rows=0 WHERE competence=%s",
            (competence,),
        )
        lock_conn.commit()
        total = processed = 0
        file_total = len(files)
        try:
            for index, remote in enumerate(files, start=1):
                if remote.file_type == "Empresas" and all_company_basics_resolved(filter_ctx):
                    log.info(
                        "[FAST-LOAD] Todas as %s razões sociais foram encontradas; "
                        "ignorando %s e os próximos ZIPs de Empresas",
                        len(filter_ctx.matched_basics),
                        remote.name,
                    )
                    lock_conn.execute(
                        "INSERT INTO etl.files "
                        "(competence,file_name,file_type,source_url,status,rows_processed,"
                        "last_run_rows,processed_at,activity_at) "
                        "VALUES (%s,%s,%s,%s,'success',0,0,now(),now()) "
                        "ON CONFLICT (competence,file_name) DO UPDATE SET "
                        "status='success',last_run_rows=0,"
                        "processed_at=now(),activity_at=now(),error_message=NULL",
                        (competence, remote.name, remote.file_type, remote.url),
                    )
                    processed += 1
                    lock_conn.execute(
                        "UPDATE etl.runs SET files_processed=%s WHERE id=%s",
                        (processed, run_id),
                    )
                    lock_conn.commit()
                    continue
                source_size, source_last_modified = source.metadata(remote)
                log.info(
                    "[%s/%s] %s (%s) — remoto %s",
                    index,
                    file_total,
                    remote.name,
                    remote.file_type,
                    _fmt_remote_size(source_size),
                )
                if remote.file_type == "Estabelecimentos" and candidate_limit_reached(filter_ctx):
                    # O orçamento de candidatos já foi preenchido. Baixar os
                    # demais ZIPs de estabelecimentos só gastaria minutos para
                    # produzir zero linhas; registre-os como concluídos neste ciclo.
                    log.info(
                        "[FAST-LEAD] Limite de %s candidatos atingido; ignorando %s",
                        filter_ctx.max_candidates,
                        remote.name,
                    )
                    lock_conn.execute(
                        "INSERT INTO etl.files "
                        "(competence,file_name,file_type,source_url,source_size,"
                        "source_last_modified,status,rows_processed) "
                        "VALUES (%s,%s,%s,%s,%s,%s,'pending',0) "
                        "ON CONFLICT (competence,file_name) DO NOTHING",
                        (
                            competence,
                            remote.name,
                            remote.file_type,
                            remote.url,
                            source_size,
                            source_last_modified,
                        ),
                    )
                    lock_conn.commit()
                    continue
                existing = lock_conn.execute(
                    "SELECT status,source_size,source_last_modified,rows_processed,scanned_rows,"
                    "skipped_rows "
                    "FROM etl.files "
                    "WHERE competence=%s AND file_name=%s",
                    (competence, remote.name),
                ).fetchone()
                unchanged = (
                    existing
                    and existing[0] == "success"
                    and (
                        (source_size is None or existing[1] == source_size)
                        and (source_last_modified is None or existing[2] == source_last_modified)
                    )
                )
                completed_establishment = bool(
                    unchanged
                    and remote.file_type == "Estabelecimentos"
                    and int(existing[4] or 0) > 0
                )
                consume_backlog = bool(
                    staged_backlog and remote.file_type in {"Simples", "Empresas"}
                )
                if unchanged and (not force or completed_establishment) and not consume_backlog:
                    log.info("Já processado: %s", remote.name)
                    continue
                resume_row = 0
                same_source = bool(
                    existing
                    and (source_size is None or existing[1] == source_size)
                    and (source_last_modified is None or existing[2] == source_last_modified)
                )
                if (
                    remote.file_type == "Estabelecimentos"
                    and same_source
                    and existing[0] in {"partial", "processing"}
                ):
                    resume_row = int(existing[4] or 0)
                    if resume_row:
                        log.info(
                            "[FAST-LEAD] Retomando %s após %s linhas",
                            remote.name,
                            f"{resume_row:,}".replace(",", "."),
                        )
                previous_loaded = int(existing[3] or 0) if resume_row else 0
                previous_skipped = int(existing[5] or 0) if resume_row else 0
                lock_conn.execute(
                    "INSERT INTO etl.files "
                    "(competence,file_name,file_type,source_url,source_size,"
                    "source_last_modified,status,activity_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'downloading',now()) "
                    "ON CONFLICT (competence,file_name) DO UPDATE SET "
                    "source_size=EXCLUDED.source_size, "
                    "source_last_modified=EXCLUDED.source_last_modified, "
                    "status='downloading',downloaded_bytes=0,rows_processed=%s,scanned_rows=%s,"
                    "skipped_rows=%s,last_run_rows=0,activity_at=now(),error_message=NULL",
                    (
                        competence,
                        remote.name,
                        remote.file_type,
                        remote.url,
                        source_size,
                        source_last_modified,
                        previous_loaded,
                        resume_row,
                        previous_skipped,
                    ),
                )
                lock_conn.commit()

                def ingest(path, sha256, size):
                    log.info(
                        "Processando %s (%s baixados, sha256=%s…)",
                        remote.name,
                        fmt_bytes(size),
                        sha256[:12],
                    )
                    lock_conn.execute(
                        "UPDATE etl.files SET sha256=%s,source_size=%s,downloaded_at=now(),"
                        "downloaded_bytes=%s,status='processing',activity_at=now() "
                        "WHERE competence=%s AND file_name=%s",
                        (sha256, size, size, competence, remote.name),
                    )
                    lock_conn.commit()

                    def report_load_progress(scanned: int, matched: int, skipped: int) -> None:
                        lock_conn.execute(
                            "UPDATE etl.files SET rows_processed=%s,scanned_rows=%s,"
                            "skipped_rows=%s,activity_at=now() "
                            "WHERE competence=%s AND file_name=%s",
                            (
                                previous_loaded + matched,
                                scanned,
                                previous_skipped + skipped,
                                competence,
                                remote.name,
                            ),
                        )
                        lock_conn.execute(
                            "UPDATE etl.runs SET rows_processed=%s WHERE id=%s",
                            (total + matched, run_id),
                        )
                        lock_conn.commit()

                    return load_zip(
                        lock_conn,
                        path,
                        remote.file_type,
                        competence,
                        settings.chunk_size,
                        label=remote.name,
                        filter_ctx=filter_ctx,
                        log_progress_every=settings.log_progress_every,
                        progress_callback=report_load_progress,
                        start_row=resume_row,
                        stop_at_candidate_limit=remote.file_type == "Estabelecimentos",
                    )

                log.info("Baixando %s …", remote.name)

                def report_download_progress(downloaded_bytes: int) -> None:
                    lock_conn.execute(
                        "UPDATE etl.files SET downloaded_bytes=%s,activity_at=now() "
                        "WHERE competence=%s AND file_name=%s",
                        (downloaded_bytes, competence, remote.name),
                    )
                    lock_conn.commit()

                if settings.keep_downloads:
                    path = settings.data_dir / competence / remote.name
                    sha256, size = source.download(
                        remote,
                        path,
                        settings.download_chunk_bytes,
                        report_download_progress,
                    )
                    rows = ingest(path, sha256, size)
                else:
                    with source.temporary_download(
                        remote,
                        settings.download_chunk_bytes,
                        report_download_progress,
                    ) as (
                        path,
                        sha256,
                        size,
                    ):
                        rows = ingest(path, sha256, size)

                rows_loaded = int(rows)
                file_status = "success" if rows.completed else "partial"
                lock_conn.execute(
                    "UPDATE etl.files SET status=%s,rows_processed=%s,"
                    "scanned_rows=%s,skipped_rows=%s,"
                    "last_run_rows=%s,"
                    "processed_at=CASE WHEN %s='success' THEN now() ELSE NULL END,"
                    "activity_at=now() "
                    "WHERE competence=%s AND file_name=%s",
                    (
                        file_status,
                        previous_loaded + rows_loaded,
                        rows.scanned_rows,
                        previous_skipped + rows.skipped_rows,
                        rows_loaded,
                        file_status,
                        competence,
                        remote.name,
                    ),
                )
                if rows.completed:
                    processed += 1
                total += rows_loaded
                lock_conn.execute(
                    "UPDATE etl.runs SET files_processed=%s,rows_processed=%s WHERE id=%s",
                    (processed, total, run_id),
                )
                lock_conn.commit()
                log.info(
                    "%s %s (%s linhas; cursor %s)",
                    "Concluído" if rows.completed else "Lote preenchido em",
                    remote.name,
                    rows_loaded,
                    f"{rows.scanned_rows:,}".replace(",", "."),
                )
                if after_file:
                    try:
                        after_file(lock_conn, remote, rows_loaded)
                    except Exception:
                        lock_conn.rollback()
                        log.exception(
                            "[FAST-LEAD] Funil incremental falhou após %s; "
                            "a carga principal continuará",
                            remote.name,
                        )
            discard_unresolved_staging(lock_conn, filter_ctx)
            lock_conn.execute(
                "UPDATE etl.runs SET status='success',finished_at=now(),"
                "files_processed=%s,rows_processed=%s WHERE id=%s",
                (processed, total, run_id),
            )
            lock_conn.commit()
            if processed == 0 and file_total > 0 and not force:
                log.warning(
                    "Nenhum arquivo novo (%s já concluídos). Para incrementar com filtros "
                    "atualizados sem apagar a base, rode com --force",
                    file_total,
                )
            return total
        except Exception as exc:
            lock_conn.rollback()
            db.record_run_failure(run_id, processed, total, exc)
            if "DiskFull" in type(exc).__name__ or "No space left on device" in str(exc):
                raise RuntimeError(
                    "Disco do Supabase/PostgreSQL cheio. Use filtros CNAE ou faça upgrade do plano."
                ) from exc
            raise
