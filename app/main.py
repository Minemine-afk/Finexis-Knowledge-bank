"""FastAPI app: a JSON API under /api (also what a CRM integration would call) and a small HTML UI.

Identity is a PROTOTYPE stand-in: the `X-KB-User` header (API) or a `kb_user` cookie (UI) names
a row in `users`. Replace with company SSO before real use; nothing else needs to change,
because access control lives in the database, keyed on the user id.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import db
from .extract import MIME_TYPES
from .ingest import METADATA_FIELDS, add_link, ingest_file, merge_metadata, storage_dir
from .search import DOC_COLUMNS, Filters, facets, search, search_table_rows

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    db.close()


app = FastAPI(title="Finexis Knowledge Bank", version="0.1.0", lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "100")) * 1024 * 1024


# --- Identity ---------------------------------------------------------------------------------

def current_user(request: Request) -> dict | None:
    name = request.headers.get("x-kb-user") or request.cookies.get("kb_user")
    return db.user_by_name(name) if name else None


def require_user(user: Annotated[dict | None, Depends(current_user)]) -> dict:
    if user is None:
        raise HTTPException(401, "Unknown user. Send X-KB-User (prototype auth).")
    return user


User = Annotated[dict, Depends(require_user)]


def filters_from(issuer: str | None = None, product_type: str | None = None,
                 document_type: str | None = None, visibility: str | None = None, mine: bool = False,
                 date_from: date | None = None, date_to: date | None = None,
                 crm_entity_type: str | None = None, crm_entity_id: str | None = None) -> Filters:
    blank = lambda v: v or None  # empty form fields arrive as ""
    if visibility and visibility not in ("shared", "private"):
        raise HTTPException(422, "visibility must be shared or private")
    return Filters(blank(issuer), blank(product_type), blank(document_type), blank(visibility), mine,
                   date_from, date_to, blank(crm_entity_type), blank(crm_entity_id))


FilterDep = Annotated[Filters, Depends(filters_from)]


# --- API --------------------------------------------------------------------------------------

@app.get("/api/search")
def api_search(user: User, filters: FilterDep, q: str = "",
               limit: Annotated[int, Query(ge=1, le=100)] = 20, offset: Annotated[int, Query(ge=0)] = 0):
    with db.as_user(user["id"]) as conn:
        return search(conn, user["id"], q, filters, limit, offset)


@app.get("/api/tables/search")
def api_table_search(user: User, filters: FilterDep, q: str,
                     limit: Annotated[int, Query(ge=1, le=200)] = 50):
    with db.as_user(user["id"]) as conn:
        return search_table_rows(conn, q, filters, limit)


@app.get("/api/facets")
def api_facets(user: User):
    with db.as_user(user["id"]) as conn:
        return facets(conn)


def _get_document(conn, doc_id: int) -> dict:
    doc = conn.execute(f"""SELECT {DOC_COLUMNS}, d.meta_source, d.extraction, d.sha256, d.size_bytes, d.created_at
                           FROM documents d JOIN users u ON u.id = d.owner_id WHERE d.id = %s""",
                       (doc_id,)).fetchone()
    if doc is None:  # missing and not-yours look the same on purpose
        raise HTTPException(404, "Document not found")
    doc["links"] = conn.execute(
        "SELECT system, entity_type, entity_id FROM document_links WHERE document_id = %s ORDER BY id",
        (doc_id,)).fetchall()
    doc["tables"] = conn.execute(
        "SELECT id, page_no, table_no, header, n_rows, n_cols, extractor FROM doc_tables "
        "WHERE document_id = %s ORDER BY page_no, table_no", (doc_id,)).fetchall()
    return doc


@app.get("/api/documents/{doc_id}")
def api_document(user: User, doc_id: int):
    with db.as_user(user["id"]) as conn:
        return _get_document(conn, doc_id)


@app.get("/api/documents/{doc_id}/pages/{page_no}")
def api_page(user: User, doc_id: int, page_no: int):
    with db.as_user(user["id"]) as conn:
        page = conn.execute("SELECT page_no, label, text, ocr FROM pages WHERE document_id = %s AND page_no = %s",
                            (doc_id, page_no)).fetchone()
    if page is None:
        raise HTTPException(404, "Page not found")
    return page


@app.get("/api/tables/{table_id}/rows")
def api_table_rows(user: User, table_id: int):
    with db.as_user(user["id"]) as conn:
        rows = conn.execute("SELECT row_no, cells, record FROM table_rows WHERE table_id = %s ORDER BY row_no",
                            (table_id,)).fetchall()
    if not rows:
        raise HTTPException(404, "Table not found")
    return rows


@app.get("/api/documents/{doc_id}/file")
def api_file(user: User, doc_id: int):
    with db.as_user(user["id"]) as conn:
        doc = conn.execute("SELECT storage_path, file_name, mime_type FROM documents WHERE id = %s",
                           (doc_id,)).fetchone()
    if doc is None:
        raise HTTPException(404, "Document not found")
    path = storage_dir() / doc["storage_path"]
    if not path.is_file():
        raise HTTPException(410, "Original file is missing from storage")
    # inline so the browser's PDF viewer honours #page=N
    return FileResponse(path, media_type=doc["mime_type"], filename=doc["file_name"], content_disposition_type="inline")


@app.post("/api/documents", status_code=201)
async def api_upload(
    user: User,
    file: UploadFile = File(...),
    visibility: str = Form("private"),
    title: str = Form(""),
    issuer: str = Form(""), product_type: str = Form(""), policy_name: str = Form(""),
    effective_date: str = Form(""), document_type: str = Form(""), version: str = Form(""),
    crm_entity_type: str = Form(""), crm_entity_id: str = Form(""),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in MIME_TYPES:
        raise HTTPException(415, f"Accepted types: {', '.join(MIME_TYPES)}")
    if visibility not in ("shared", "private"):
        raise HTTPException(422, "visibility must be shared or private")

    metadata = dict(title=title, issuer=issuer, product_type=product_type, policy_name=policy_name,
                    effective_date=effective_date, document_type=document_type, version=version)
    links = [dict(system="crm", entity_type=crm_entity_type or "client", entity_id=crm_entity_id)] \
        if crm_entity_id.strip() else []

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"upload{suffix}"
        size = 0
        with src.open("wb") as f:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File too large")
                f.write(chunk)
        try:
            with db.as_user(user["id"]) as conn:
                doc_id, created = ingest_file(conn, user["id"], src, file.filename, visibility, metadata, links)
        except ValueError as e:
            raise HTTPException(422, str(e))
    return {"id": doc_id, "created": created}


class MetadataPatch(BaseModel):
    title: str | None = None
    visibility: str | None = None
    issuer: str | None = None
    product_type: str | None = None
    policy_name: str | None = None
    effective_date: str | None = None
    document_type: str | None = None
    version: str | None = None


@app.patch("/api/documents/{doc_id}")
def api_update_metadata(user: User, doc_id: int, patch: MetadataPatch):
    """Owner-only (enforced by row-level security). A corrected field is marked as uploader-supplied."""
    changes = patch.model_dump(exclude_unset=True)
    if changes.get("visibility") not in (None, "shared", "private"):
        raise HTTPException(422, "visibility must be shared or private")
    try:
        values, _ = merge_metadata({k: v for k, v in changes.items() if k in METADATA_FIELDS}, {})
    except ValueError as e:
        raise HTTPException(422, str(e))
    sets, params = [], {"id": doc_id}
    for key in [k for k in changes if k not in METADATA_FIELDS] + list(values):
        sets.append(f"{key} = %({key})s" + ("::visibility" if key == "visibility" else ""))
        params[key] = values.get(key, changes[key])
    cleared = [k for k in changes if k in METADATA_FIELDS and k not in values]  # sent as "" or null
    sets += [f"{k} = NULL" for k in cleared]
    if not sets:
        raise HTTPException(422, "Nothing to update")
    source = {k: "uploader" for k in values}
    with db.as_user(user["id"]) as conn:
        row = conn.execute(
            f"""UPDATE documents SET {', '.join(sets)}, meta_source = (meta_source - %(cleared)s::text[]) || %(source)s,
                       updated_at = now()
                WHERE id = %(id)s RETURNING id""",
            {**params, "cleared": cleared, "source": Jsonb(source)}).fetchone()
    if row is None:
        raise HTTPException(404, "Document not found or not yours")
    return {"id": doc_id, "updated": sorted(changes)}


class LinkIn(BaseModel):
    entity_type: str
    entity_id: str
    system: str = "crm"


@app.post("/api/documents/{doc_id}/links", status_code=201)
def api_add_link(user: User, doc_id: int, link: LinkIn):
    with db.as_user(user["id"]) as conn:
        if conn.execute("SELECT 1 FROM documents WHERE id = %s AND owner_id = %s",
                        (doc_id, user["id"])).fetchone() is None:
            raise HTTPException(404, "Document not found or not yours")
        add_link(conn, doc_id, user["id"], link.system, link.entity_type, link.entity_id)
    return {"ok": True}


@app.get("/api/crm/{entity_type}/{entity_id}/documents")
def api_crm_documents(user: User, entity_type: str, entity_id: str):
    """What a CRM screen would call: the documents linked to one of its records that this user may see."""
    with db.as_user(user["id"]) as conn:
        return conn.execute(
            f"""SELECT {DOC_COLUMNS} FROM documents d JOIN users u ON u.id = d.owner_id
                JOIN document_links l ON l.document_id = d.id
                WHERE l.system = 'crm' AND l.entity_type = %s AND l.entity_id = %s
                ORDER BY d.effective_date DESC NULLS LAST, d.id""",
            (entity_type.lower(), entity_id)).fetchall()


@app.get("/api/health")
def health():
    with db.as_user(None) as conn:
        conn.execute("SELECT 1")
    return {"ok": True}


# --- UI ---------------------------------------------------------------------------------------

@app.get("/login")
def login(u: str):
    if db.user_by_name(u) is None:
        raise HTTPException(404, "No such user")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("kb_user", u, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("kb_user")
    return resp


@app.get("/", response_class=HTMLResponse)
def ui_search(request: Request, filters: FilterDep, user: Annotated[dict | None, Depends(current_user)],
              q: str = "", offset: Annotated[int, Query(ge=0)] = 0, uploaded: int | None = None):
    if user is None:
        return templates.TemplateResponse(request, "login.html", {"users": db.all_users()})
    with db.as_user(user["id"]) as conn:
        result = search(conn, user["id"], q, filters, 20, offset)
        rows = search_table_rows(conn, q, filters, 10) if q.strip() else []
        options = facets(conn)
    for r in result["results"]:
        r["snippet"] = Markup(r["snippet"])  # already escaped in search.highlight
    return templates.TemplateResponse(request, "search.html", {
        "user": user, "q": q, "f": filters, "result": result, "table_rows": rows,
        "options": options, "offset": offset, "uploaded": uploaded,
    })


@app.post("/upload")
async def ui_upload(request: Request, user: User, file: UploadFile = File(...),
                    visibility: str = Form("private"), title: str = Form(""), issuer: str = Form(""),
                    product_type: str = Form(""), policy_name: str = Form(""), effective_date: str = Form(""),
                    document_type: str = Form(""), version: str = Form(""),
                    crm_entity_type: str = Form(""), crm_entity_id: str = Form("")):
    out = await api_upload(user, file, visibility, title, issuer, product_type, policy_name,
                           effective_date, document_type, version, crm_entity_type, crm_entity_id)
    return RedirectResponse(f"/documents/{out['id']}", status_code=303)


@app.get("/documents/{doc_id}", response_class=HTMLResponse)
def ui_document(request: Request, user: User, doc_id: int):
    with db.as_user(user["id"]) as conn:
        doc = _get_document(conn, doc_id)
        for t in doc["tables"]:
            t["rows"] = conn.execute("SELECT cells FROM table_rows WHERE table_id = %s ORDER BY row_no LIMIT 200",
                                     (t["id"],)).fetchall()
    return templates.TemplateResponse(request, "document.html", {"user": user, "doc": doc})
