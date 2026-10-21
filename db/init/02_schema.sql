-- Knowledge bank schema. Runs once, as the Postgres superuser, on an empty database.
-- Tables are owned by kb_owner; the web app connects as kb_app, which is subject to
-- row-level security (it is neither the owner nor a superuser, and has no BYPASSRLS).

SET ROLE kb_owner;

CREATE TABLE users (
    id           serial PRIMARY KEY,
    username     text NOT NULL UNIQUE,
    display_name text NOT NULL
);

CREATE TYPE visibility AS ENUM ('shared', 'private');

-- One row per original file. The file itself lives on disk (STORAGE_DIR) and is the source
-- of truth; everything below is derived from it and can be rebuilt by re-ingesting.
CREATE TABLE documents (
    id             bigserial PRIMARY KEY,
    owner_id       integer    NOT NULL REFERENCES users(id),
    visibility     visibility NOT NULL DEFAULT 'private',

    title          text NOT NULL,
    file_name      text NOT NULL,          -- name as uploaded
    storage_path   text NOT NULL,          -- relative to STORAGE_DIR
    mime_type      text NOT NULL,
    sha256         text NOT NULL,
    size_bytes     bigint NOT NULL,
    page_count     integer NOT NULL DEFAULT 0,

    -- Metadata fields from the brief. NULL means unknown, never guessed silently:
    -- meta_source records which values came from the uploader and which were inferred.
    issuer         text,
    product_type   text,
    policy_name    text,
    effective_date date,
    document_type  text,
    version        text,
    meta_source    jsonb NOT NULL DEFAULT '{}',

    extraction     jsonb NOT NULL DEFAULT '{}',   -- per-file extraction report
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),

    meta_tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(policy_name, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(issuer, '') || ' ' || coalesce(product_type, '') || ' ' ||
                                         coalesce(document_type, '')), 'B')
    ) STORED,

    UNIQUE (owner_id, sha256)
);
CREATE INDEX documents_meta_tsv_idx ON documents USING gin (meta_tsv);
CREATE INDEX documents_owner_idx    ON documents (owner_id);
CREATE INDEX documents_filters_idx  ON documents (issuer, product_type, document_type);
CREATE INDEX documents_effective_idx ON documents (effective_date);

-- Extracted text, one row per page (a sheet for spreadsheets, the whole body for Word files).
CREATE TABLE pages (
    id          bigserial PRIMARY KEY,
    document_id bigint  NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no     integer NOT NULL,            -- 1-based, matches the PDF viewer's #page=N
    label       text,                        -- sheet name etc.
    text        text NOT NULL,
    ocr         boolean NOT NULL DEFAULT false,
    image_count integer NOT NULL DEFAULT 0,
    drawing_count integer NOT NULL DEFAULT 0, -- vector paths; a high count usually means a chart
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (document_id, page_no)
);
CREATE INDEX pages_tsv_idx ON pages USING gin (tsv);

-- Tables found in a document, stored as structured rows.
CREATE TABLE doc_tables (
    id          bigserial PRIMARY KEY,
    document_id bigint  NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no     integer NOT NULL,
    table_no    integer NOT NULL,            -- position on the page, 1-based
    header      jsonb   NOT NULL,            -- array of column names
    n_rows      integer NOT NULL,
    n_cols      integer NOT NULL,
    extractor   text    NOT NULL,            -- pdfplumber | docx | openpyxl
    UNIQUE (document_id, page_no, table_no)
);

CREATE TABLE table_rows (
    id          bigserial PRIMARY KEY,
    table_id    bigint  NOT NULL REFERENCES doc_tables(id) ON DELETE CASCADE,
    document_id bigint  NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    row_no      integer NOT NULL,
    cells       jsonb   NOT NULL,            -- array of strings, same order as header
    record      jsonb   NOT NULL,            -- {header: value}
    row_text    text    NOT NULL,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', row_text)) STORED
);
CREATE INDEX table_rows_tsv_idx ON table_rows USING gin (tsv);
CREATE INDEX table_rows_table_idx ON table_rows (table_id, row_no);
CREATE INDEX table_rows_record_idx ON table_rows USING gin (record jsonb_path_ops);

-- Links from a document to records in other systems, starting with the company CRM
-- (e.g. system='crm', entity_type='client', entity_id='C-10293'). Ids are the other
-- system's own, stored as text, so the CRM can ask "which documents belong to client X"
-- without the knowledge bank having to copy its data.
CREATE TABLE document_links (
    id          bigserial PRIMARY KEY,
    document_id bigint  NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    system      text    NOT NULL,            -- crm
    entity_type text    NOT NULL,            -- client | policy | case ...
    entity_id   text    NOT NULL,
    created_by  integer REFERENCES users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, system, entity_type, entity_id)
);
CREATE INDEX document_links_entity_idx ON document_links (system, entity_type, entity_id);

-- Every search, with the exact result list, so a result can be reproduced and explained later.
CREATE TABLE search_log (
    id         bigserial PRIMARY KEY,
    user_id    integer REFERENCES users(id),
    query      text NOT NULL,
    filters    jsonb NOT NULL,
    result_ids bigint[] NOT NULL,           -- document ids, in ranked order
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Every chat question and answer, with the exact passages the answer was given, so an answer
-- shown to an adviser can be traced back to its sources later.
CREATE TABLE chat_log (
    id         bigserial PRIMARY KEY,
    user_id    integer REFERENCES users(id),
    question   text  NOT NULL,
    answer     text  NOT NULL,
    mode       text  NOT NULL,                -- llm | passages | no_results | llm_error
    model      text,
    sources    jsonb NOT NULL,                -- [{document_id, page_no, score}], in the order cited
    filters    jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------------
-- Row-level security. The app sets app.user_id per transaction (SET LOCAL); with it unset a
-- connection sees shared documents only.

CREATE FUNCTION kb_current_user() RETURNS integer
    LANGUAGE sql STABLE AS $$ SELECT nullif(current_setting('app.user_id', true), '')::integer $$;

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY documents_read  ON documents FOR SELECT
    USING (visibility = 'shared' OR owner_id = kb_current_user());
CREATE POLICY documents_write ON documents FOR ALL
    USING (owner_id = kb_current_user()) WITH CHECK (owner_id = kb_current_user());

-- Child rows follow their document. The subquery is itself filtered by the documents policies.
ALTER TABLE pages ENABLE ROW LEVEL SECURITY;
CREATE POLICY pages_read  ON pages FOR SELECT
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id));
CREATE POLICY pages_write ON pages FOR ALL
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id AND d.owner_id = kb_current_user()))
    WITH CHECK (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id AND d.owner_id = kb_current_user()));

ALTER TABLE doc_tables ENABLE ROW LEVEL SECURITY;
CREATE POLICY doc_tables_read  ON doc_tables FOR SELECT
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id));
CREATE POLICY doc_tables_write ON doc_tables FOR ALL
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id AND d.owner_id = kb_current_user()))
    WITH CHECK (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id AND d.owner_id = kb_current_user()));

ALTER TABLE table_rows ENABLE ROW LEVEL SECURITY;
CREATE POLICY table_rows_read  ON table_rows FOR SELECT
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id));
CREATE POLICY table_rows_write ON table_rows FOR ALL
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id AND d.owner_id = kb_current_user()))
    WITH CHECK (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id AND d.owner_id = kb_current_user()));

ALTER TABLE document_links ENABLE ROW LEVEL SECURITY;
CREATE POLICY document_links_read  ON document_links FOR SELECT
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id));
CREATE POLICY document_links_write ON document_links FOR ALL
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id AND d.owner_id = kb_current_user()))
    WITH CHECK (EXISTS (SELECT 1 FROM documents d WHERE d.id = document_id AND d.owner_id = kb_current_user()));

-- The search log is append-only for the app; people can read back their own searches.
ALTER TABLE search_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY search_log_insert ON search_log FOR INSERT WITH CHECK (user_id IS NOT DISTINCT FROM kb_current_user());
CREATE POLICY search_log_read   ON search_log FOR SELECT USING (user_id = kb_current_user());

ALTER TABLE chat_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY chat_log_insert ON chat_log FOR INSERT WITH CHECK (user_id IS NOT DISTINCT FROM kb_current_user());
CREATE POLICY chat_log_read   ON chat_log FOR SELECT USING (user_id = kb_current_user());

GRANT SELECT ON users TO kb_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON documents, pages, doc_tables, table_rows, document_links TO kb_app;
GRANT SELECT, INSERT ON search_log, chat_log TO kb_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO kb_app;
GRANT EXECUTE ON FUNCTION kb_current_user() TO kb_app;

RESET ROLE;
