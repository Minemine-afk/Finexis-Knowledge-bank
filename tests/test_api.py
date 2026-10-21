"""End-to-end tests against a real Postgres initialised from db/init (see CLAUDE.md).
Skipped when DATABASE_URL is not set. Each test uses unique words, so the database needn't be empty."""
import os
import uuid

import psycopg
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["STORAGE_DIR"] = str(tmp_path_factory.mktemp("files"))
    from fastapi.testclient import TestClient
    from app import db
    from app.main import app
    yield TestClient(app)
    db.close()


def as_(user):
    return {"X-KB-User": user}


def token():
    # letters only, so the English stemmer leaves it alone
    return "zq" + "".join(c for c in uuid.uuid4().hex if c.isalpha())[:10]


def upload(client, path, user="alice", **form):
    with path.open("rb") as f:
        r = client.post("/api/documents", headers=as_(user), files={"file": (path.name, f)}, data=form)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_requires_user(client):
    assert client.get("/api/search?q=x").status_code == 401
    assert client.get("/api/search?q=x", headers=as_("nobody")).status_code == 401


def test_private_documents_are_invisible_to_others(client, tmp_path):
    from tests.fixtures import policy_pdf
    t = token()
    doc_id = upload(client, policy_pdf(tmp_path / "p.pdf", t), visibility="private")

    mine = client.get(f"/api/search?q={t}", headers=as_("alice")).json()["results"]
    assert [r["id"] for r in mine] == [doc_id]
    assert client.get(f"/api/search?q={t}", headers=as_("bob")).json()["results"] == []
    for url in (f"/api/documents/{doc_id}", f"/api/documents/{doc_id}/file", f"/api/documents/{doc_id}/pages/1"):
        assert client.get(url, headers=as_("bob")).status_code == 404, url
    assert client.get(f"/api/tables/search?q=premium {t}", headers=as_("bob")).json() == []
    assert client.patch(f"/api/documents/{doc_id}", headers=as_("bob"), json={"title": "x"}).status_code == 404


def test_shared_documents_are_visible_to_all(client, tmp_path):
    from tests.fixtures import policy_pdf
    t = token()
    doc_id = upload(client, policy_pdf(tmp_path / "p.pdf", t), visibility="shared")
    hits = client.get(f"/api/search?q={t}", headers=as_("bob")).json()["results"]
    assert [h["id"] for h in hits] == [doc_id]
    assert client.get(f"/api/documents/{doc_id}/file", headers=as_("bob")).status_code == 200


def test_search_result_shape_and_filters(client, tmp_path):
    from tests.fixtures import policy_pdf
    t = token()
    doc_id = upload(client, policy_pdf(tmp_path / "p.pdf", t), product_type="Hospital plan")

    r = client.get(f"/api/search?q=cosmetic {t}", headers=as_("alice")).json()["results"]
    # "cosmetic" is only on page 2 and the token only on page 1: AND across the doc fails per page,
    # so search the page-2 word alone, filtered to this document's product type
    assert r == []
    r = client.get("/api/search", params={"q": "cosmetic surgery", "product_type": "Hospital plan", "mine": "true"},
                   headers=as_("alice")).json()["results"]
    hit = next(h for h in r if h["id"] == doc_id)
    assert hit["best_page"] == 2
    assert "<mark>Cosmetic</mark>" in hit["snippet"]
    assert hit["issuer"] == "Singlife"  # inferred
    assert hit["effective_date"] == "2026-04-01"

    none = client.get("/api/search", params={"q": t, "issuer": "Manulife"}, headers=as_("alice")).json()
    assert none["results"] == []
    dated = client.get("/api/search", params={"q": t, "date_from": "2026-05-01"}, headers=as_("alice")).json()
    assert dated["results"] == []


def test_search_is_deterministic(client, tmp_path):
    from tests.fixtures import policy_pdf
    t = token()
    for i in range(3):
        upload(client, policy_pdf(tmp_path / f"p{i}.pdf", f"{t} copy{i}"))
    runs = [[r["id"] for r in client.get(f"/api/search?q={t}", headers=as_("alice")).json()["results"]]
            for _ in range(3)]
    assert len(runs[0]) == 3 and runs[0] == runs[1] == runs[2]


def test_table_rows_are_searchable(client, tmp_path):
    from tests.fixtures import rates_xlsx
    t = token()
    doc_id = upload(client, rates_xlsx(tmp_path / "r.xlsx", t))
    rows = client.get(f"/api/tables/search?q={t}", headers=as_("alice")).json()
    assert rows[0]["document_id"] == doc_id
    assert rows[0]["record"] == {"Plan": "Endowment 10", "Term": "10 years", "Rate": f"2.08% {t}"}


def test_metadata_patch_and_sources(client, tmp_path):
    from tests.fixtures import policy_docx
    doc_id = upload(client, policy_docx(tmp_path / "b.docx", token()), effective_date="2025-12-01")
    doc = client.get(f"/api/documents/{doc_id}", headers=as_("alice")).json()
    assert doc["meta_source"] == {"issuer": "inferred", "document_type": "inferred", "effective_date": "uploader"}

    r = client.patch(f"/api/documents/{doc_id}", headers=as_("alice"),
                     json={"issuer": "Manulife", "document_type": None, "visibility": "shared"})
    assert r.status_code == 200, r.text
    doc = client.get(f"/api/documents/{doc_id}", headers=as_("bob")).json()
    assert doc["document_type"] is None and doc["visibility"] == "shared"
    assert doc["meta_source"] == {"issuer": "uploader", "effective_date": "uploader"}
    assert client.patch(f"/api/documents/{doc_id}", headers=as_("alice"),
                        json={"effective_date": "someday"}).status_code == 422


def test_duplicate_upload_is_noop(client, tmp_path):
    from tests.fixtures import policy_docx
    p = policy_docx(tmp_path / "b.docx", token())
    first = upload(client, p)
    with p.open("rb") as f:
        r = client.post("/api/documents", headers=as_("alice"), files={"file": ("b.docx", f)})
    assert (r.json()["id"], r.json()["created"]) == (first, False)


def test_crm_links(client, tmp_path):
    from tests.fixtures import policy_pdf
    client_id = f"C-{uuid.uuid4().hex[:8]}"
    doc_id = upload(client, policy_pdf(tmp_path / "p.pdf", token()), crm_entity_type="client", crm_entity_id=client_id)
    docs = client.get(f"/api/crm/client/{client_id}/documents", headers=as_("alice")).json()
    assert [d["id"] for d in docs] == [doc_id]
    assert client.get(f"/api/crm/client/{client_id}/documents", headers=as_("bob")).json() == []  # private
    hits = client.get("/api/search", params={"crm_entity_id": client_id}, headers=as_("alice")).json()["results"]
    assert [h["id"] for h in hits] == [doc_id]
    assert client.post(f"/api/documents/{doc_id}/links", headers=as_("bob"),
                       json={"entity_type": "client", "entity_id": "X"}).status_code == 404


def test_rls_blocks_writes_into_other_peoples_documents(client, tmp_path):
    """Even raw SQL as the app role cannot add pages to someone else's (shared) document."""
    from tests.fixtures import policy_pdf
    doc_id = upload(client, policy_pdf(tmp_path / "p.pdf", token()), visibility="shared")
    from app import db
    bob = db.user_by_name("bob")["id"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.as_user(bob) as conn:
            conn.execute("INSERT INTO pages (document_id, page_no, text) VALUES (%s, 99, 'x')", (doc_id,))
    with db.as_user(bob) as conn:
        assert conn.execute("UPDATE documents SET title = 'x' WHERE id = %s", (doc_id,)).rowcount == 0
    with db.as_user(None) as conn:  # no user: shared only
        assert conn.execute("SELECT count(*) AS n FROM documents WHERE visibility = 'private'").fetchone()["n"] == 0


def test_rejects_unsupported_upload(client):
    r = client.post("/api/documents", headers=as_("alice"), files={"file": ("x.exe", b"MZ")})
    assert r.status_code == 415


def test_ui_pages_render(client, tmp_path):
    from tests.fixtures import policy_pdf
    t = token()
    doc_id = upload(client, policy_pdf(tmp_path / "p.pdf", t))
    assert "Who are you?" in client.get("/").text
    client.cookies.set("kb_user", "alice")
    chat = client.get("/").text
    assert "Ask the knowledge bank" in chat and "CRM" not in chat
    page = client.get(f"/search?q={t}").text
    assert "CRM" not in page
    assert f"/documents/{doc_id}" in page and "<mark>" in page
    detail = client.get(f"/documents/{doc_id}").text
    assert "Annual premium (S$)" in detail and "inferred" in detail
    client.cookies.clear()


def test_chat_without_model_returns_cited_passages(client, tmp_path, monkeypatch):
    from tests.fixtures import policy_pdf
    monkeypatch.delenv("LLM_URL", raising=False)
    t = token()
    doc_id = upload(client, policy_pdf(tmp_path / "p.pdf", t), visibility="private")
    # a natural question: not every word is in the document, retrieval still finds the page
    res = client.post("/api/chat", headers=as_("alice"),
                      json={"message": f"How much does {t} cost to cover hospitalisation?"}).json()
    assert res["mode"] == "passages"
    assert res["sources"][0]["document_id"] == doc_id
    assert res["sources"][0]["url"] == f"/api/documents/{doc_id}/file#page=1"
    assert "<mark>" in res["sources"][0]["snippet"]
    # bob can't see alice's private document through chat either
    res = client.post("/api/chat", headers=as_("bob"), json={"message": f"{t} hospitalisation"}).json()
    assert doc_id not in [s["document_id"] for s in res["sources"]]


def test_chat_with_model_gets_only_visible_passages_and_is_logged(client, tmp_path, monkeypatch):
    from app import db, llm
    from tests.fixtures import policy_docx
    monkeypatch.setenv("LLM_URL", "http://llm.invalid")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    seen = {}

    def fake_complete(messages):
        seen["messages"] = messages
        return "Early stage pays 25% [1]."
    monkeypatch.setattr(llm, "complete", fake_complete)

    t = token()
    doc_id = upload(client, policy_docx(tmp_path / "b.docx", t), visibility="shared")
    history = [{"role": "user", "content": f"Tell me about {t}"}, {"role": "assistant", "content": "Sure."}]
    res = client.post("/api/chat", headers=as_("bob"),
                      json={"message": "does it cover early stages?", "history": history}).json()
    assert res["mode"] == "llm" and res["answer"] == "Early stage pays 25% [1]."
    assert res["sources"][0]["document_id"] == doc_id      # follow-up used the earlier question's words
    prompt = seen["messages"][-1]["content"]
    assert prompt.startswith("Sources:\n\n[1] ") and t in prompt and prompt.endswith("Question: does it cover early stages?")
    assert seen["messages"][1] == history[0]

    with db.as_user(db.user_by_name("bob")["id"]) as conn:
        row = conn.execute("SELECT question, model, sources FROM chat_log WHERE id = %s", (res["id"],)).fetchone()
    assert row["model"] == "test-model" and row["sources"][0]["document_id"] == doc_id
    with db.as_user(db.user_by_name("alice")["id"]) as conn:  # chat logs are private to their user
        assert conn.execute("SELECT 1 FROM chat_log WHERE id = %s", (res["id"],)).fetchone() is None


def test_chat_model_failure_falls_back_to_passages(client, tmp_path, monkeypatch):
    from app import llm
    from tests.fixtures import policy_pdf
    monkeypatch.setenv("LLM_URL", "http://llm.invalid")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setattr(llm, "complete", lambda m: (_ for _ in ()).throw(ConnectionError("down")))
    t = token()
    upload(client, policy_pdf(tmp_path / "p.pdf", t))
    res = client.post("/api/chat", headers=as_("alice"), json={"message": f"{t} premium"}).json()
    assert res["mode"] == "llm_error" and res["sources"]


def test_chat_no_results(client):
    res = client.post("/api/chat", headers=as_("alice"), json={"message": token()}).json()
    assert res["mode"] == "no_results" and res["sources"] == []
