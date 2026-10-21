"""Quick Supabase connectivity + schema check.  Run:  python check_db.py

Confirms the .env credentials reach the project and whether the AutoMark tables
exist yet. Prints a clear next step either way. Prints no secrets."""

import config
from db import SupabaseStore, SupabaseError


def main():
    cfg = config.supabase_config()
    if not cfg["url"] or not cfg["secret"]:
        print("No Supabase config in .env (SUPABASE_URL / SUPABASE_SECRET_KEY).")
        return
    print("Project ref :", cfg["ref"])
    print("REST URL    :", cfg["url"] + "/rest/v1/")
    try:
        s = SupabaseStore()
        s.ping()
        n = len(s.list_assignments())
        print("Connection  : OK")
        print("Schema      : tables exist (assignments has %d rows)" % n)
        print("\nReady. Set AUTOMARK_DB=supabase in .env and restart: python app.py")
    except SupabaseError as e:
        msg = str(e)
        if "PGRST205" in msg or "Could not find the table" in msg:
            print("Connection  : OK (keys are valid)")
            print("Schema      : NOT created yet")
            print("\nNext: open the Supabase SQL editor and run "
                  "supabase/migrations/0001_init.sql, then re-run this check.")
        else:
            print("Connection  : FAILED ->", msg)


if __name__ == "__main__":
    main()
