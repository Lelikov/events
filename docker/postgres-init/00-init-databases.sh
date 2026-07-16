#!/bin/bash
# Provisions per-service databases + login roles on the shared dev Postgres
# instance, mirroring the prod managed-PG model (one host, per-DB login).
# Runs once on a fresh data volume via /docker-entrypoint-initdb.d.
set -euo pipefail

create_db_role() {
  local db="$1" user="$2" pass="$3"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    DO \$\$ BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$user') THEN
        CREATE ROLE "$user" LOGIN PASSWORD '$pass';
      END IF;
    END \$\$;
    SELECT 'CREATE DATABASE "$db" OWNER "$user"'
      WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db')\gexec
    GRANT ALL PRIVILEGES ON DATABASE "$db" TO "$user";
SQL
  echo "  provisioned db=$db owner=$user"
}

echo "Provisioning shared-instance databases + roles..."
create_db_role "${PG_SAVER_DB:-event_saver}"         "${PG_SAVER_USER:-event_saver}"         "${PG_SAVER_PASSWORD:-event_saver}"
create_db_role "${PG_USERS_DB:-event_users}"         "${PG_USERS_USER:-event_users}"         "${PG_USERS_PASSWORD:-event_users}"
create_db_role "${PG_NOTIFIER_DB:-event_notifier}"   "${PG_NOTIFIER_USER:-event_notifier}"   "${PG_NOTIFIER_PASSWORD:-event_notifier}"
create_db_role "${PG_SHORTENER_DB:-event_shortener}" "${PG_SHORTENER_USER:-event_shortener}" "${PG_SHORTENER_PASSWORD:-event_shortener}"
create_db_role "${PG_DB_SYNC_DB:-event_db_sync}"     "${PG_DB_SYNC_USER:-event_db_sync}"     "${PG_DB_SYNC_PASSWORD:-event_db_sync}"
create_db_role "${PG_CALCOM_DB:-calcom}"             "${PG_CALCOM_USER:-calcom}"             "${PG_CALCOM_PASSWORD:-calcom}"
create_db_role "${PG_SCHEDULING_DB:-event_scheduling}" "${PG_SCHEDULING_USER:-event_scheduling}" "${PG_SCHEDULING_PASSWORD:-event_scheduling}"
create_db_role "${PG_ORGANIZER_DB:-event_organizer}"   "${PG_ORGANIZER_USER:-event_organizer}"   "${PG_ORGANIZER_PASSWORD:-event_organizer}"

# cal.com lives on the shared instance too. Load the dev fixture INTO the calcom DB
# under the calcom role (so it owns the tables and event-db-sync can create its triggers).
# Real data is loaded separately via scripts/copy_calcom.sh.
if [ -f /calcom-init/01-schema.sql ]; then
  echo "  loading cal.com fixture schema into ${PG_CALCOM_DB:-calcom}..."
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${PG_CALCOM_DB:-calcom}" \
    -c "SET ROLE \"${PG_CALCOM_USER:-calcom}\";" -f /calcom-init/01-schema.sql
fi
echo "Done."
