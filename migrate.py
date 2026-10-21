"""Apply supabase/migrations/*.sql to the Supabase Postgres database.

Connects via the session pooler (host/user/password from .env) with psycopg2.
Run:  python migrate.py        Prints no secrets.
"""

import glob
import os
import sys

import config

HERE = os.path.dirname(os.path.abspath(__file__))
MIG_DIR = os.path.join(HERE, "supabase", "migrations")


def apply():
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is not installed (pip install psycopg2-binary).")
        return 1
    p = config.pg_params()
    if not p["host"] or not p["password"]:
        print("No Postgres connection configured in .env "
              "(SUPABASE_DB_HOST / SUPABASE_DB_PASSWORD).")
        return 1
    files = sorted(glob.glob(os.path.join(MIG_DIR, "*.sql")))
    if not files:
        print("No migration files in", MIG_DIR)
        return 1
    print("Connecting to", p["host"], "as", p["user"], "...")
    try:
        conn = psycopg2.connect(
            host=p["host"], port=p["port"], user=p["user"],
            password=p["password"], dbname=p["dbname"],
            sslmode="require", connect_timeout=30)
    except Exception as e:
        print("Connection FAILED:", e)
        return 1
    conn.autocommit = True
    cur = conn.cursor()
    for f in files:
        sql = open(f, encoding="utf-8").read()
        print("Applying", os.path.basename(f), "...")
        cur.execute(sql)
        print("  ok")
    cur.close()
    conn.close()
    print("All migrations applied.")
    return 0


if __name__ == "__main__":
    sys.exit(apply())
