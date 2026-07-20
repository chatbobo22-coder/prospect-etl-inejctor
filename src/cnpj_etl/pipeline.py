import logging
from dataclasses import replace

from .loader import load_zip

log = logging.getLogger(__name__)


def prepare_run_settings(settings, db, auto_bootstrap: bool = False):
    if not auto_bootstrap:
        return settings, False
    with db.connect() as conn:
        if db.needs_initial_load(conn):
            log.info("Base vazia detectada — iniciando primeira carga completa (todos os tipos)")
            return replace(settings, include_types=frozenset()), True
    log.info("Base já populada — sincronização incremental (apenas arquivos novos ou alterados)")
    return settings, False


def run(
    settings,
    db,
    source,
    competence: str | None = None,
    force: bool = False,
    auto_bootstrap: bool = False,
):
    settings, _ = prepare_run_settings(settings, db, auto_bootstrap)
    competence = competence or source.latest_competence()
    all_files = source.list_files(competence)
    files = [
        f for f in all_files if not settings.include_types or f.file_type in settings.include_types
    ]
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
        try:
            for remote in files:
                source_size, source_last_modified = source.metadata(remote)
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
                    )

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
                    "UPDATE etl.files SET status='success',rows_processed=%s,processed_at=now() WHERE competence=%s AND file_name=%s",
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
            return total
        except Exception as exc:
            lock_conn.rollback()
            lock_conn.execute(
                "UPDATE etl.runs SET status='failed',finished_at=now(),files_processed=%s,"
                "rows_processed=%s,error_message=%s WHERE id=%s",
                (processed, total, str(exc)[:4000], run_id),
            )
            lock_conn.commit()
            raise
