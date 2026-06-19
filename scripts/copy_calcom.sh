#!/bin/bash
# One-time / re-runnable copy of a real cal.com DB into the dev compose `calcom`
# database (shared postgres instance). Reads the source with the host's pg_dump,
# pipes the dump into the compose postgres container. Real data is NOT committed.
#
# Usage:  scripts/copy_calcom.sh
#         CALCOM_SOURCE_DSN=postgresql://user:pass@host:port/db scripts/copy_calcom.sh
set -euo pipefail

SRC="${CALCOM_SOURCE_DSN:-postgresql://calendar:@127.0.0.1:5445/calendso}"
SUPER="${PG_SUPERUSER:-postgres}"
CAL_DB="${PG_CALCOM_DB:-calcom}"
CAL_USER="${PG_CALCOM_USER:-calcom}"

echo ">> Wiping fixture schema in '${CAL_DB}' and loading real cal.com from ${SRC} ..."
docker compose exec -T postgres psql -U "$SUPER" -d "$CAL_DB" -v ON_ERROR_STOP=1 \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO \"$CAL_USER\";"

pg_dump "$SRC" --no-owner --no-privileges \
  | docker compose exec -T postgres psql -U "$SUPER" -d "$CAL_DB" -v ON_ERROR_STOP=1 >/dev/null

# pg_dump --no-owner loads everything as the superuser. Hand ownership of the
# tables/sequences to the calcom role so event-db-sync (which connects AS calcom)
# can manage its NOTIFY triggers — DROP/CREATE TRIGGER requires table ownership,
# not just GRANTs. Extensions stay superuser-owned (calcom only uses them).
docker compose exec -T postgres psql -U "$SUPER" -d "$CAL_DB" -v ON_ERROR_STOP=1 -c "
GRANT ALL ON SCHEMA public TO \"$CAL_USER\";
DO \$\$
DECLARE r record;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO %I', r.tablename, '$CAL_USER');
  END LOOP;
  FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO %I', r.sequencename, '$CAL_USER');
  END LOOP;
  FOR r IN SELECT table_name FROM information_schema.views WHERE table_schema = 'public' LOOP
    EXECUTE format('ALTER VIEW public.%I OWNER TO %I', r.table_name, '$CAL_USER');
  END LOOP;
END
\$\$;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO \"$CAL_USER\";"

tables=$(docker compose exec -T postgres psql -U "$SUPER" -d "$CAL_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
echo ">> Done. '${CAL_DB}' now has ${tables} tables."
