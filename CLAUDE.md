# Financial Document Knowledge Bank: project brief

Handoff from a planning chat, kept here as the standing context for this repo.

## Goal

Build a knowledge bank that ingests large volumes of financial documents (policies, charts, tables)
and lets users retrieve them by keyword search or metadata filters. No AI model is required for
the core; models are optional later additions.

## Context and scale (for the pitch)

- Financial services company, 1,500+ non-technical users, 100,000+ documents eventually
- Preference: fully on-prem/private; budget about SGD 6k/month; AWS hybrid only if needed
- Needs: shared document pool plus private spaces for client data; employees can upload their own documents
- Company has its own CRM (storage only); client documents mostly sit with individual employees.
  **Integrate with the CRM eventually** (requested 2026-10-21): keep CRM-linkable ids and a stable API,
  but nothing may depend on the CRM being connected.
- Only hardware available now: a single ThinkPad (specs unknown, to be confirmed). Fine for a prototype, not for production
- Compliance/IT must confirm where client data may be stored before any cloud use

## Architecture decisions

1. Original files (PDF, Word, Excel) stored on disk or file share. Source of truth; search results link back to file and page.
2. Database holds extracted text, metadata fields, extracted table rows, and the search index.
3. Optional: rendered page images for pages containing charts.
4. Prototype database: **Postgres 16 in Docker** from the start (no later migration; row-level security).

### Phase 1: no AI model (built)

- Ingestion: PyMuPDF for text, pdfplumber for tables, Tesseract OCR for scanned PDFs
- Metadata fields: issuer/insurer, product type, policy name, effective date, document type, version, owner, visibility (shared/private)
- Search: keyword (full-text, ranked) plus metadata filters
- UI: small FastAPI app. **The main screen is a chat** (requested 2026-10-21): ask a question,
  get an answer built only from retrieved passages, with numbered sources linking to the page.
  Keyword search with filters is still at `/search`.
- Index chart titles, captions and axis labels as text; store tables as structured rows

### Phase 2: light model

- Local embeddings (bge-small, all-MiniLM or nomic-embed, CPU is fine) for hybrid keyword + semantic search (pgvector)

### Phase 3: local LLM (wired in, optional)

- Small local LLM (3B-8B via Ollama or llama.cpp) for Q&A over retrieved passages, always showing source page.
  The chat works without it (shows the passages); set `LLM_MODEL` to turn written answers on.

### Database notes

- Production: Postgres (tsvector + GIN; pgvector later), on a company server
- Supabase = Postgres + RLS, auth, storage, auto APIs. Hosted puts data in the cloud; self-hosted keeps it on-prem at a maintenance cost. Plain Postgres + FastAPI is simpler if the extras aren't needed.

## Cross-cutting requirements

- Access control built in from day one: every document has owner and visibility; every query filters by it (row-level security in Postgres)
- Scheduled backups
- Deterministic, auditable search (important for compliance material)

## Open questions

- ThinkPad specs (RAM, CPU, GPU/VRAM, free disk)
- Compliance ruling on data location (on-prem vs hosted Supabase vs AWS)
- Supabase vs plain Postgres for production
- Real sample set: 20-30 representative documents to judge table-extraction quality

---

# Working in this repo

**This repository is public. Never commit real documents, client data, or extraction reports.**
`.gitignore` blocks `data/`, `samples/`, `reports/` and all PDF/DOCX/XLSX files; tests generate
their own synthetic documents (`tests/fixtures.py`).

`index.html` + `README.md` at the root are the earlier static "Policy Desk" product-catalogue demo,
independent of the backend below.

## Layout

| Path | What |
|---|---|
| `app/extract.py` | File → pages, tables, suggested metadata. Pure, no DB. |
| `app/ingest.py` | Hash, store file (content-addressed under `STORAGE_DIR`), extract, insert in one transaction. |
| `app/search.py` | Full-text search + filters, table-row search, facets, search log, chat passage retrieval. |
| `app/llm.py` | Optional local model (Ollama or OpenAI-compatible): prompt, call, fallbacks. |
| `app/main.py` | FastAPI: JSON API under `/api`, chat UI at `/`, search UI at `/search`. |
| `app/db.py` | Connection pool; `as_user(id)` sets `app.user_id` for RLS. |
| `db/init/` | Roles, schema + RLS policies, dev users. Run once by the Postgres image on an empty volume. |
| `scripts/ingest_folder.py` | Bulk ingest + extraction report (`--dry-run` needs no DB). |
| `scripts/backup.sh` | `pg_dump` + rsync of original files, keeps 14 dumps. |

## Access control

The app connects as `kb_app` (not owner, no BYPASSRLS). Each request runs in a transaction that
sets `app.user_id`; policies in `db/init/02_schema.sql` allow reading `shared` documents and your
own, and writing only your own. Child tables (pages, tables, rows, links) follow their document.
With no user set, only shared documents are visible. Do not add queries that bypass `db.as_user`.

Identity is a **prototype stand-in** (`X-KB-User` header / `kb_user` cookie naming a `users` row).
Replace with company SSO; for the CRM, add a service credential that maps to a user or acts on
behalf of one, so RLS still applies.

## Chat

`POST /api/chat {message, history, filters}`:
1. `retrieve_passages` runs full-text search as the user (RLS applies) with the question's terms
   ORed, so natural questions still match; short follow-ups are combined with the previous question.
2. `llm.answer` sends the top passages (numbered) to the model with instructions to answer only
   from them and cite `[n]`; temperature 0. No model configured, no results, or a model error each
   return the passages with a clear `mode` instead of failing.
3. Question, answer, model and the exact sources are written to `chat_log` (readable only by the
   asker) for audit.

The model never sees a document the user couldn't open themselves.

## CRM integration points (ready, unused until the CRM is connected)

Hidden in the UI for now; set `SHOW_CRM=1` to show the CRM record fields again.

- `document_links (system, entity_type, entity_id)` ties a document to CRM records by the CRM's own ids.
- Upload accepts `crm_entity_type` / `crm_entity_id`; `POST /api/documents/{id}/links` adds more.
- `GET /api/crm/{entity_type}/{entity_id}/documents` and the `crm_entity_id` search filter are what a CRM screen would call.

## Search behaviour

`websearch_to_tsquery('english', q)` (supports `"phrases"`, `or`, `-exclude`), matched against page
text and weighted document metadata; one result per document showing its best page. Ordering is
`score, effective_date, id`, so identical inputs give identical lists. Every search is written to
`search_log` with its result ids.

## Running

```sh
cp .env.example .env            # set real passwords
docker compose up -d            # http://localhost:8000
# optional written answers in chat:
docker compose --profile llm up -d && docker compose exec ollama ollama pull qwen2.5:7b-instruct
# then set LLM_MODEL=qwen2.5:7b-instruct in .env and `docker compose up -d app`
python -m scripts.ingest_folder ./samples --dry-run                       # check extraction only
docker compose exec app python -m scripts.ingest_folder /data/inbox --owner alice --visibility shared
```

Tests (Postgres needed for `tests/test_api.py`; it is skipped without `DATABASE_URL`):

```sh
pip install -r requirements-dev.txt     # and tesseract-ocr from the OS
# on a scratch DB: run db/init/01_roles.sh (with KB_APP_PASSWORD), 02_schema.sql, 03_dev_users.sql
DATABASE_URL=postgresql://kb_app:<pw>@localhost:5432/knowledge_bank pytest -q
```

Schema changes: there is no migration tool yet; `db/init` only runs on an empty volume. Add one
(e.g. plain numbered SQL files applied by a script) before the first real data goes in.
