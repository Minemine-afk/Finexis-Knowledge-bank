"""Store a file, extract it, and write the results to the database in one transaction."""
from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from . import storage
from .extract import Extracted, extract, parse_date

METADATA_FIELDS = ("issuer", "product_type", "policy_name", "effective_date", "document_type", "version")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def merge_metadata(given: dict, suggested: dict) -> tuple[dict, dict]:
    """Uploader-supplied values win; inferred ones fill gaps. Returns (values, source per field)."""
    values, source = {}, {}
    for key in METADATA_FIELDS:
        value = given.get(key)
        if isinstance(value, str):
            value = value.strip() or None
        if value is not None:
            values[key], source[key] = value, "uploader"
        elif suggested.get(key) is not None:
            values[key], source[key] = suggested[key], "inferred"
    if isinstance(values.get("effective_date"), str):
        parsed = parse_date(values["effective_date"])
        if parsed is None:
            raise ValueError(f"Unreadable effective_date: {values['effective_date']!r} (use YYYY-MM-DD)")
        values["effective_date"] = parsed
    return values, source


def _clean(s: str) -> str:
    return s.replace("\x00", "")


def ingest_file(conn: psycopg.Connection, owner_id: int, src: Path, file_name: str | None = None,
                visibility: str = "private", metadata: dict | None = None,
                links: list[dict] | None = None, ocr: bool = True) -> tuple[int, bool]:
    """Returns (document id, created). Re-uploading a file the owner already has is a no-op.

    `src` must carry the real extension (.pdf/.docx/.xlsx); `file_name` is the name to show.
    """
    file_name = file_name or src.name
    sha = sha256_of(src)
    existing = conn.execute("SELECT id FROM documents WHERE owner_id = %s AND sha256 = %s",
                            (owner_id, sha)).fetchone()
    if existing:
        return existing["id"], False

    ex: Extracted = extract(src, ocr=ocr)
    metadata = dict(metadata or {})
    values, source = merge_metadata(metadata, ex.metadata)
    title = (metadata.get("title") or "").strip() or Path(file_name).stem.replace("_", " ")
    rel = f"{sha[:2]}/{sha}{src.suffix.lower()}"
    storage.save(src, rel, ex.mime_type)

    doc_id = conn.execute(
        """INSERT INTO documents (owner_id, visibility, title, file_name, storage_path, mime_type, sha256,
                                  size_bytes, page_count, issuer, product_type, policy_name, effective_date,
                                  document_type, version, meta_source, extraction)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (owner_id, visibility, title, file_name, rel, ex.mime_type, sha, src.stat().st_size, ex.page_count,
         values.get("issuer"), values.get("product_type"), values.get("policy_name"),
         values.get("effective_date"), values.get("document_type"), values.get("version"),
         Jsonb(source), Jsonb(ex.report())),
    ).fetchone()["id"]

    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO pages (document_id, page_no, label, text, ocr, image_count, drawing_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            [(doc_id, p.page_no, p.label, _clean(p.text), p.ocr, p.image_count, p.drawing_count) for p in ex.pages],
        )
        for t in ex.tables:
            table_id = cur.execute(
                """INSERT INTO doc_tables (document_id, page_no, table_no, header, n_rows, n_cols, extractor)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (doc_id, t.page_no, t.table_no, Jsonb(t.header), len(t.rows), len(t.header), t.extractor),
            ).fetchone()["id"]
            cur.executemany(
                """INSERT INTO table_rows (table_id, document_id, row_no, cells, record, row_text)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                [(table_id, doc_id, i, Jsonb([_clean(c) for c in row]),
                  Jsonb({h: _clean(c) for h, c in zip(t.header, row)}), _clean(" | ".join(row)))
                 for i, row in enumerate(t.rows, start=1)],
            )
    for link in links or []:
        add_link(conn, doc_id, owner_id, **link)
    return doc_id, True


def add_link(conn: psycopg.Connection, document_id: int, user_id: int, system: str,
             entity_type: str, entity_id: str) -> None:
    conn.execute(
        """INSERT INTO document_links (document_id, system, entity_type, entity_id, created_by)
           VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        (document_id, system.strip().lower(), entity_type.strip().lower(), entity_id.strip(), user_id),
    )
