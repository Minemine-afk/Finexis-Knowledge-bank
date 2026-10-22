#!/bin/sh
# Creates the two database roles. Runs once, from the Postgres image's init hook.
#   kb_owner  owns the schema (migrations); cannot log in
#   kb_app    the web app's login; subject to row-level security
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE kb_owner NOLOGIN;
CREATE ROLE kb_app LOGIN PASSWORD '${KB_APP_PASSWORD:?set KB_APP_PASSWORD}' NOSUPERUSER NOBYPASSRLS;
GRANT CREATE, USAGE ON SCHEMA public TO kb_owner;
GRANT USAGE ON SCHEMA public TO kb_app;
GRANT kb_owner TO "$POSTGRES_USER";
SQL
