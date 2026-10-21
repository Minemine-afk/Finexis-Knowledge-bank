#!/bin/sh
# One-time database setup on a new Supabase project (the equivalent of db/init for Docker).
#   SUPABASE_DB_URL='postgresql://postgres:<db password>@db.<ref>.supabase.co:5432/postgres' \
#   KB_APP_PASSWORD='<new password for the app role>' ./scripts/supabase_init.sh
# Then create a PRIVATE storage bucket named "documents" in the Supabase dashboard.
set -eu
: "${SUPABASE_DB_URL:?}" "${KB_APP_PASSWORD:?}"
cd "$(dirname "$0")/../db/init"
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE kb_owner NOLOGIN;
CREATE ROLE kb_app LOGIN PASSWORD '$KB_APP_PASSWORD' NOSUPERUSER NOBYPASSRLS;
GRANT CREATE, USAGE ON SCHEMA public TO kb_owner;
GRANT USAGE ON SCHEMA public TO kb_app;
GRANT kb_owner TO postgres;
SQL
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f 02_schema.sql -f 03_dev_users.sql
# Supabase exposes the public schema through its auto-generated REST API. This app doesn't use
# that API, so make sure its roles can't reach these tables at all.
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 <<SQL
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION kb_current_user() FROM anon, authenticated, PUBLIC;
GRANT EXECUTE ON FUNCTION kb_current_user() TO kb_app;
SQL
echo "Done. App connection: postgresql://kb_app.<project-ref>:<KB_APP_PASSWORD>@<pooler-host>:5432/postgres"
