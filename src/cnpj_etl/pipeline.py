import logging
from dataclasses import replace
import os

from .filters import FILE_LOAD_ORDER, FilterContext
from .ibge_population import ensure_municipios_populacao, load_allowed_municipios
from .loader import load_zip
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
                uf_msg = f", UFs={','.join(sorted(settings.filter_ufs))}" if settings.filter_ufs else ""
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
                extras = []
                if settings.filter_require_nome_fantasia:
                    extras.append("nome fantasia obrigatório")
                if settings.filter_require_telefone:
                    extras.append("telefone válido obrigatório")
                if settings.filter_min_population > 0:
                    extras.append(f"municípios >= {settings.filter_min_population:,} hab".replace(",", "."))
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

    return FilterContext(
        cnaes=settings.filter_cnaes,
        active_only=settings.filter_active_only,
        ufs=settings.filter_ufs,
        include_secondary_cnae=settings.filter_include_secondary_cnae,
        require_nome_fantasia=settings.filter_require_nome_fantasia,
        require_telefone=settings.filter_require_telefone,
        min_population=settings.filter_min_population,
        allowed_municipios=allowed_municipios,
    )


def sort_files(files, filter_ctx: FilterContext):
    if not filter_ctx.enabled:
        return files
    return sorted(files, key=lambda f: (FILE_LOAD_ORDER.get(f.file_type, 99), f.name))


def run(
    settings,
    db,
    source,
    competence: str | None = None,
    force: bool = False,
    auto_bootstrap: bool = False,
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
    all_files = source.list_files(competence)
    allowed = settings.resolved_file_types()
    files = [f for f in all_files if not allowed or f.file_type in allowed]
    files = sort_files(files, filter_ctx or FilterContext(frozenset()))
    if settings.keep_downloads:
        target = settings.data_dir / competence
        target.mkdir(parents=True, exist_ok=True)

    with db.connect() as lock_conn:
        if not db.acquire_lock(lock_conn):
            log.warning("Outra execução está ativa; encerrando.")
            return 0
        run_id = lock_conn.execute(
            "INSERT INTO etl.runs (competence,status,files_total) VALUES (%s,'running',%s) RETURNING id",
            (competence, len(files)),
        ).fetchone()[0]
        lock_conn.commit()
        total = processed = 0
        file_total = len(files)
        try:
            for index, remote in enumerate(files, start=1):
                source_size, source_last_modified = source.metadata(remote)
                log.info(
                    "[%s/%s] %s (%s) — remoto %s",
                    index,
                    file_total,
                    remote.name,
                    remote.file_type,
                    _fmt_remote_size(source_size),
                )
                existing = lock_conn.execute(
                    "SELECT status,source_size,source_last_modified FROM etl.files "
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
                if unchanged and not force:
                    log.info("Já processado: %s", remote.name)
                    continue
                lock_conn.execute(
                    "INSERT INTO etl.files "
                    "(competence,file_name,file_type,source_url,source_size,source_last_modified,status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'downloading') "
                    "ON CONFLICT (competence,file_name) DO UPDATE SET "
                    "source_size=EXCLUDED.source_size, "
                    "source_last_modified=EXCLUDED.source_last_modified, "
                    "status='downloading',error_message=NULL",
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

                def ingest(path, sha256, size):
                    log.info(
                        "Processando %s (%s baixados, sha256=%s…)",
                        remote.name,
                        fmt_bytes(size),
                        sha256[:12],
                    )
                    lock_conn.execute(
                        "UPDATE etl.files SET sha256=%s,source_size=%s,downloaded_at=now(),"
                        "status='processing' WHERE competence=%s AND file_name=%s",
                        (sha256, size, competence, remote.name),
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
                    )

                log.info("Baixando %s …", remote.name)
                if settings.keep_downloads:
                    path = settings.data_dir / competence / remote.name
                    sha256, size = source.download(remote, path, settings.download_chunk_bytes)
                    rows = ingest(path, sha256, size)
                else:
                    with source.temporary_download(
                        remote, settings.download_chunk_bytes
                    ) as (path, sha256, size):
                        rows = ingest(path, sha256, size)

                lock_conn.execute(
                    "UPDATE etl.files SET status='success',rows_processed=%s,processed_at=now() "
                    "WHERE competence=%s AND file_name=%s",
                    (rows, competence, remote.name),
                )
                lock_conn.commit()
                processed += 1
                total += rows
                log.info("Concluído %s (%s linhas)", remote.name, rows)
            lock_conn.execute(
                "UPDATE etl.runs SET status='success',finished_at=now(),"
                "files_processed=%s,rows_processed=%s WHERE id=%s",
                (processed, total, run_id),
            )
            lock_conn.commit()
            if processed == 0 and file_total > 0:
                log.warning(
                    "Nenhum arquivo reprocessado (%s marcados como concluídos). "
                    "Para recarregar com filtros novos: reset-load --yes + run --auto --force",
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
