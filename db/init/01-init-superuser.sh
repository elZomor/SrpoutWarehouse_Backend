#!/bin/bash
# Runs automatically via docker-entrypoint-initdb.d on first container startup
# (only when the postgres data volume is empty). Creates an additional
# PostgreSQL superuser role, separate from POSTGRES_USER, using credentials
# from .env (DB_SUPERUSER_NAME / DB_SUPERUSER_PASSWORD).
#
# Postgres does not expand shell/env vars inside plain .sql files, so this
# shell wrapper substitutes them before handing the SQL to psql.
set -euo pipefail

: "${DB_SUPERUSER_NAME:?DB_SUPERUSER_NAME must be set (see .env)}"
: "${DB_SUPERUSER_PASSWORD:?DB_SUPERUSER_PASSWORD must be set (see .env)}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_SUPERUSER_NAME}') THEN
            CREATE ROLE "${DB_SUPERUSER_NAME}" WITH LOGIN SUPERUSER PASSWORD '${DB_SUPERUSER_PASSWORD}';
        END IF;
    END
    \$\$;
EOSQL
