"""Storage backends and the site password. No database needed."""
import base64

import httpx
import pytest

from app import storage


def test_local_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "files"))
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.4 test")
    storage.save(src, "ab/abc.pdf", "application/pdf")
    assert (tmp_path / "files/ab/abc.pdf").read_bytes() == b"%PDF-1.4 test"
    assert storage.serve("ab/abc.pdf", "application/pdf", "a.pdf").status_code == 200
    with pytest.raises(Exception) as e:
        storage.serve("ab/missing.pdf", "application/pdf", "m.pdf")
    assert e.value.status_code == 410


def test_supabase_upload_and_signed_link(tmp_path, monkeypatch):
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        if "/object/sign/" in req.url.path:
            return httpx.Response(200, json={"signedURL": "/object/sign/documents/ab/abc.pdf?token=t"})
        return httpx.Response(200, json={"Key": "documents/ab/abc.pdf"})

    monkeypatch.setenv("STORAGE_BACKEND", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.co/")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setattr(storage, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF")

    storage.save(src, "ab/abc.pdf", "application/pdf")
    up = calls[0]
    assert str(up.url) == "https://proj.supabase.co/storage/v1/object/documents/ab/abc.pdf"
    assert up.headers["authorization"] == "Bearer service-key" and up.headers["x-upsert"] == "true"
    assert up.content == b"%PDF"

    resp = storage.serve("ab/abc.pdf", "application/pdf", "a.pdf")
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://proj.supabase.co/storage/v1/object/sign/documents/ab/abc.pdf?token=t"


def test_s3_presigned_link(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "documents")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s")
    resp = storage.serve("ab/abc.pdf", "application/pdf", "a.pdf")
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://acct.r2.cloudflarestorage.com/documents/ab/abc.pdf?")


def test_site_password(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setenv("SITE_PASSWORD", "s3cret")
    c = TestClient(app)
    assert c.get("/login?u=alice").status_code == 401
    bad = base64.b64encode(b"x:wrong").decode()
    assert c.get("/login?u=alice", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    good = base64.b64encode(b"anyone:s3cret").decode()
    r = c.get("/api/search?q=x", headers={"Authorization": f"Basic {good}"})
    assert r.status_code != 401 or "X-KB-User" in r.text   # past the gate; app auth answers now
