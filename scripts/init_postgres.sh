#!/bin/bash
# Runs once when the PostgreSQL data directory is first initialised.
# POSTGRES_USER and POSTGRES_DB (agri_agent) are already created by the
# official Docker image entrypoint before this script executes.
# We connect to POSTGRES_DB explicitly — psql defaults to a DB named after
# the user (agri) which does not exist; POSTGRES_DB (agri_agent) does.
set -e

echo "[init] Creating langflow database..."

if psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -lqt | cut -d'|' -f1 | grep -qw langflow; then
    echo "[init] langflow already exists — skipping."
else
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE langflow OWNER $POSTGRES_USER;"
    echo "[init] langflow created."
fi
