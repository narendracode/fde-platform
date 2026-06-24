"""Wait for PostgreSQL to accept connections on the specific database.

Exits 0 when ready, 1 after timeout.
Uses psycopg2 (already in project deps) — no extra installs needed.
"""
import os
import sys
import time

import psycopg2

dsn = os.environ.get("DATABASE_URL_SYNC", "")
if not dsn:
    print("[wait_for_db] DATABASE_URL_SYNC not set", flush=True)
    sys.exit(1)

print("[wait_for_db] Waiting for database...", flush=True)
for attempt in range(1, 31):
    try:
        conn = psycopg2.connect(dsn, connect_timeout=3)
        conn.close()
        print(f"[wait_for_db] Ready after {attempt} attempt(s).", flush=True)
        sys.exit(0)
    except psycopg2.OperationalError as exc:
        print(f"[wait_for_db] [{attempt}/30] {exc}", flush=True)
        time.sleep(2)

print("[wait_for_db] Timed out after 60 seconds.", flush=True)
sys.exit(1)
