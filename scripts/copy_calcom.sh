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

docker compose exec -T postgres psql -U "$SUPER" -d "$CAL_DB" -v ON_ERROR_STOP=1 \
  -c "GRANT ALL ON ALL TABLES IN SCHEMA public TO \"$CAL_USER\";
      GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO \"$CAL_USER\";
      ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO \"$CAL_USER\";"

tables=$(docker compose exec -T postgres psql -U "$SUPER" -d "$CAL_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
echo ">> Done. '${CAL_DB}' now has ${tables} tables."
