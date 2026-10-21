"""Where original files live: local disk (default), Supabase Storage, or any S3-compatible store
(AWS S3, Cloudflare R2, Supabase's S3 endpoint).

Paths are content-addressed (`ab/abcdef….pdf`), so a write is idempotent and a stored file never
changes. Access is checked by the app (through row-level security) before a file is served; the
Supabase bucket should be private, and the service key must stay on the server.

    STORAGE_BACKEND=local      STORAGE_DIR=./data/files
    STORAGE_BACKEND=supabase   SUPABASE_URL=https://<project>.supabase.co
                               SUPABASE_SERVICE_KEY=<service role key>   SUPABASE_BUCKET=documents
    STORAGE_BACKEND=s3         S3_BUCKET=documents   S3_ENDPOINT_URL=<omit for AWS; R2: https://<acct>.r2.cloudflarestorage.com>
                               AWS_ACCESS_KEY_ID=…   AWS_SECRET_ACCESS_KEY=…   AWS_REGION=auto|ap-southeast-1
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import httpx
from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

SIGNED_URL_SECONDS = 300


def backend() -> str:
    return os.environ.get("STORAGE_BACKEND", "local").lower()


def storage_dir() -> Path:
    return Path(os.environ.get("STORAGE_DIR", "./data/files")).resolve()


def save(src: Path, rel: str, mime_type: str) -> None:
    if backend() == "supabase":
        _supabase_upload(src, rel, mime_type)
        return
    if backend() == "s3":
        _s3().upload_file(str(src), os.environ["S3_BUCKET"], rel, ExtraArgs={"ContentType": mime_type})
        return
    dest = storage_dir() / rel
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)


def serve(rel: str, mime_type: str, file_name: str) -> Response:
    """Response for an already-authorised download: the file itself, or a short-lived signed link."""
    if backend() == "supabase":
        return RedirectResponse(_supabase_signed_url(rel), status_code=302)
    if backend() == "s3":
        return RedirectResponse(_s3().generate_presigned_url(
            "get_object", ExpiresIn=SIGNED_URL_SECONDS,
            Params={"Bucket": os.environ["S3_BUCKET"], "Key": rel, "ResponseContentType": mime_type,
                    "ResponseContentDisposition": "inline"}), status_code=302)
    path = storage_dir() / rel
    if not path.is_file():
        raise HTTPException(410, "Original file is missing from storage")
    # inline so the browser's PDF viewer honours #page=N
    return FileResponse(path, media_type=mime_type, filename=file_name, content_disposition_type="inline")


# --- Supabase Storage (REST) ------------------------------------------------------------------

def _supabase() -> tuple[str, str, str]:
    try:
        return (os.environ["SUPABASE_URL"].rstrip("/"), os.environ["SUPABASE_SERVICE_KEY"],
                os.environ.get("SUPABASE_BUCKET", "documents"))
    except KeyError as e:
        raise RuntimeError(f"STORAGE_BACKEND=supabase needs {e.args[0]}") from None


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "apikey": key}


def http_client() -> httpx.Client:  # replaced in tests
    return httpx.Client(timeout=120)


def _supabase_upload(src: Path, rel: str, mime_type: str) -> None:
    url, key, bucket = _supabase()
    with http_client() as client, src.open("rb") as f:
        r = client.post(f"{url}/storage/v1/object/{bucket}/{rel}", content=f.read(),
                        headers={**_headers(key), "Content-Type": mime_type, "x-upsert": "true"})
    r.raise_for_status()


def _supabase_signed_url(rel: str) -> str:
    url, key, bucket = _supabase()
    with http_client() as client:
        r = client.post(f"{url}/storage/v1/object/sign/{bucket}/{rel}", headers=_headers(key),
                        json={"expiresIn": SIGNED_URL_SECONDS})
    if r.status_code in (400, 404):
        raise HTTPException(410, "Original file is missing from storage")
    r.raise_for_status()
    return f"{url}/storage/v1{r.json()['signedURL']}"


# --- S3-compatible ----------------------------------------------------------------------------

def _s3():
    import boto3  # only needed for this backend

    return boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
                        region_name=os.environ.get("AWS_REGION", "auto"))
