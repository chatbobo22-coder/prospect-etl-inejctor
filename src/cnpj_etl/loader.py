import csv
from datetime import datetime
import hashlib
import io
import logging
from zipfile import ZipFile

from psycopg import sql

from .filters import should_load_row, track_estabelecimento
from .schema import DATASETS, DATE_COLUMNS

log = logging.getLogger(__name__)


def clean(value: str):
    value = value.strip()
    return value or None


def date_value(value: str):
    value = value.strip()
    if not value or value == "0" * 8:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def transform(kind: str, row: list[str], columns: list[str], competence: str):
    if len(row) < len(columns):
        row += [""] * (len(columns) - len(row))
    values = [date_value(v) if c in DATE_COLUMNS else clean(v) for c, v in zip(columns, row)]
    item = dict(zip(columns, values))
    if kind == "Empresas":
        raw = (item.get("capital_social") or "0").replace(".", "").replace(",", ".")
        try:
            item["capital_social"] = raw
        except ValueError:
            item["capital_social"] = None
    if kind == "Estabelecimentos":
        item["cnpj"] = "".join(
            [item.get("cnpj_basico") or "", item.get("cnpj_ordem") or "", item.get("cnpj_dv") or ""]
        )
    if kind == "Socios":
        identity = "|".join(str(item.get(c) or "") for c in columns)
        item["id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    if kind not in {"Cnaes", "Municipios", "Paises", "Naturezas", "Qualificacoes", "Motivos"}:
        item["source_competence"] = competence
    return item


def describe_row(kind: str, item: dict) -> str:
    if kind == "Estabelecimentos":
        return (
            f"cnpj={item.get('cnpj')} uf={item.get('uf')} "
            f"cnae={item.get('cnae_fiscal_principal')} fantasia={item.get('nome_fantasia')!r}"
        )
    if kind == "Empresas":
        return f"cnpj_basico={item.get('cnpj_basico')} razao={item.get('razao_social')!r} capital={item.get('capital_social')}"
    if kind == "Socios":
        return f"cnpj_basico={item.get('cnpj_basico')} socio={item.get('nome_socio_razao_social')!r}"
    if kind == "Simples":
        return f"cnpj_basico={item.get('cnpj_basico')} simples={item.get('opcao_simples')} mei={item.get('opcao_mei')}"
    if kind == "Cnaes":
        return f"{item.get('codigo')}={item.get('descricao')!r}"
    return str(item.get("codigo") or item.get("cnpj_basico") or item.get("cnpj") or "")[:120]


def upsert_chunk(conn, table: str, rows: list[dict], conflict: str, *, kind: str = "", label: str = ""):
    if not rows:
        return
    columns = list(rows[0])
    temp = f"tmp_{table}"
    conn.execute(
        sql.SQL("CREATE TEMP TABLE {} (LIKE cnpj.{} INCLUDING DEFAULTS) ON COMMIT DROP").format(
            sql.Identifier(temp), sql.Identifier(table)
        )
    )
    copy_stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(temp), sql.SQL(",").join(map(sql.Identifier, columns))
    )
    with conn.cursor().copy(copy_stmt) as copy:
        for row in rows:
            copy.write_row([row[c] for c in columns])
    update_cols = [c for c in columns if c != conflict]
    assignments = sql.SQL(",").join(
        sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c)) for c in update_cols
    )
    stmt = (
        sql.SQL(
            "INSERT INTO cnpj.{} ({}) SELECT {} FROM {} ON CONFLICT ({}) DO UPDATE SET {}, updated_at=now()"
        ).format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.Identifier(temp),
            sql.Identifier(conflict),
            assignments,
        )
        if table
        not in {
            "cnaes",
            "municipios",
            "paises",
            "naturezas_juridicas",
            "qualificacoes_socios",
            "motivos_situacao",
        }
        else sql.SQL(
            "INSERT INTO cnpj.{} ({}) SELECT {} FROM {} ON CONFLICT ({}) DO UPDATE SET descricao=EXCLUDED.descricao"
        ).format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.Identifier(temp),
            sql.Identifier(conflict),
        )
    )
    conn.execute(stmt)
    conn.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(temp)))
    if kind and label:
        log.info(
            "%s → cnpj.%s: lote %s registros gravados (ex: %s)",
            label,
            table,
            len(rows),
            describe_row(kind, rows[-1]),
        )


def load_zip(
    conn,
    zip_path,
    kind: str,
    competence: str,
    chunk_size: int,
    *,
    label: str | None = None,
    filter_ctx=None,
    log_progress_every: int = 50000,
) -> int:
    table, columns = DATASETS[kind]
    conflict = (
        "cnpj"
        if kind == "Estabelecimentos"
        else "id"
        if kind == "Socios"
        else "cnpj_basico"
        if kind in {"Empresas", "Simples"}
        else "codigo"
    )
    display_name = label or getattr(zip_path, "name", str(zip_path))
    count = skipped = scanned = 0
    chunk: list[dict] = []
    log.info("%s: iniciando leitura (%s → cnpj.%s)", display_name, kind, table)
    with ZipFile(zip_path) as archive:
        members = [n for n in archive.namelist() if not n.endswith("/")]
        if not members:
            raise RuntimeError(f"ZIP vazio: {display_name}")
        inner = members[0]
        log.info("%s: arquivo interno %s", display_name, inner)
        with (
            archive.open(inner) as raw,
            io.TextIOWrapper(raw, encoding="latin-1", newline="") as text,
        ):
            for row in csv.reader(text, delimiter=";", quotechar='"'):
                scanned += 1
                item = transform(kind, row, columns, competence)
                if not should_load_row(kind, item, filter_ctx):
                    skipped += 1
                else:
                    if kind == "Estabelecimentos" and filter_ctx:
                        track_estabelecimento(item, filter_ctx)
                    chunk.append(item)
                    if len(chunk) >= chunk_size:
                        upsert_chunk(
                            conn, table, chunk, conflict, kind=kind, label=display_name
                        )
                        conn.commit()
                        count += len(chunk)
                        chunk.clear()
                if scanned % log_progress_every == 0:
                    matched = count + len(chunk)
                    pct = (matched / scanned * 100) if scanned else 0
                    log.info(
                        "%s: lidas=%s | gravadas=%s | ignoradas=%s | taxa=%.2f%%",
                        display_name,
                        scanned,
                        matched,
                        skipped,
                        pct,
                    )
            if chunk:
                upsert_chunk(conn, table, chunk, conflict, kind=kind, label=display_name)
                conn.commit()
                count += len(chunk)
    log.info(
        "%s: concluído — lidas=%s gravadas=%s ignoradas=%s empresas_unicas=%s",
        display_name,
        scanned,
        count,
        skipped,
        len(filter_ctx.matched_basics) if filter_ctx else "-",
    )
    return count
