# AutoMark — setup from a fresh clone

Get a cloned copy running. The app itself is **pure Python standard library**;
the only extra Python package (`psycopg2`) is used solely to apply database
migrations. Large binaries (a JDK and some jars) are **not** committed — a helper
script downloads them.

Estimated time: ~5–10 minutes (most of it the one-time JDK download).

---

## 0. Prerequisites

- **Python 3.9+** (3.12 tested) — `python --version`
- Internet access (to download the JDK + jars, and to reach Supabase)
- *(optional)* **Docker Desktop** — only if you want the real network/file sandbox
- A **Supabase project** if you want the hosted database (otherwise it runs on a
  local JSON file with zero setup)

---

## 1. Clone

```bash
git clone <your-repo-url> automark
cd automark
```

---

## 2. Install the Python dependency (migrations only)

```bash
python -m pip install -r requirements.txt
```

---

## 3. Download the binary dependencies (JDK + jars)

These live in the git-ignored `jdk/` and `lib/` folders, so fetch them once:

```bash
python fetch_deps.py
```

This downloads:
- **Temurin JDK 21** → `jdk/` (skipped if you already have a JDK on your PATH; or
  use your own and set `AUTOMARK_JAVA_HOME` in `.env`)
- **JUnit** console launcher, **Checkstyle**, and **JaCoCo** (agent + CLI) → `lib/`

Java grading, Checkstyle style scoring, JUnit tests and coverage all depend on
these. Python-only grading works without them.

---

## 4. Create your `.env`

Copy the template and fill in your values:

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

Then edit `.env`:

| Key | What to put |
|-----|-------------|
| `SUPABASE_REF`, `SUPABASE_URL` | your project ref and `https://<ref>.supabase.co` |
| `SUPABASE_PUBLISHABLE_KEY` | the publishable (anon) key |
| `SUPABASE_SECRET_KEY` | the secret (service-role) key — **server only** |
| `SUPABASE_DB_HOST` | `aws-0-<region>.pooler.supabase.com` (session pooler) |
| `SUPABASE_DB_USER` | `postgres.<ref>` |
| `SUPABASE_DB_PASSWORD` | your database password |
| `AUTOMARK_SESSION_SECRET` | any long random string (see below) |
| `AUTOMARK_DB` | `supabase` to use Postgres, or `json` for the local file |

Generate a session secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> **Never commit `.env`** — it holds secrets and is already in `.gitignore`.
> `.env.example` (safe placeholders) is the template to share.

**No Supabase?** Set `AUTOMARK_DB=json` and skip step 5 — the app stores data in
`data/db.json` locally and needs none of the `SUPABASE_*` values.

---

## 5. Create the database schema (Supabase only)

Apply the migrations in `supabase/migrations/` (needs `SUPABASE_DB_*` in `.env`):

```bash
python migrate.py
```

(Or paste each `supabase/migrations/*.sql` into the Supabase dashboard SQL editor,
in order.) Check it worked:

```bash
python check_db.py        # → "Schema: tables exist"
```

---

## 6. Run

```bash
python app.py
```

Open <http://localhost:8000> (opens automatically; Windows users can also
double-click `run.bat`). On first run it seeds demo accounts and sample
assignments.

**Demo logins** (password `password123`): `teacher@automark.test` (teacher),
`alice@class.test` … `erin@class.test` (students).

---

## 7. Verify (optional)

```bash
python tests/marking_suite.py     # 22 grading/adversarial cases
python tests/hostile_pack.py      # 10 hostile Java programs (sandbox check)
```

---

## 8. Optional extras

**LLM feedback** — set in `.env` (Groq has a free tier; Ollama runs locally):
```
AUTOMARK_LLM_PROVIDER=groq
AUTOMARK_LLM_API_KEY=...
AUTOMARK_LLM_MODEL=llama-3.3-70b-versatile
```

**Real Docker sandbox** (network/file isolation) — needs Docker Desktop running:
```bash
docker build -t automark-grader grader/
# then in .env:  AUTOMARK_SANDBOX=docker
```
`GET /api/env` shows the active mode (`sandbox: docker | subprocess`); it falls
back to the subprocess runner if Docker isn't reachable.

---

## Troubleshooting

- **`psycopg2` import error on `migrate.py`** → `pip install -r requirements.txt`.
- **Java features missing / "No JDK found"** → run `python fetch_deps.py`, or set
  `AUTOMARK_JAVA_HOME` / `JAVA_HOME` to an existing JDK 21.
- **`migrate.py` can't connect** → check `SUPABASE_DB_HOST` (session pooler, not
  the direct `db.<ref>` host, which may be IPv6-only) and the DB password.
- **App ignores Supabase** → ensure `AUTOMARK_DB=supabase` and the tables exist
  (`python check_db.py`); it silently falls back to local JSON otherwise.
- **Reset local demo data** → delete the `data/` folder and restart.

See `README.md` for how the grading engine, auth, analytics and anti-gaming work.
