"""Tiny .env loader (no external dependency) + config accessors."""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
_loaded = False


def load_env(path=None):
    """Read KEY=VALUE lines from .env into os.environ (without overriding
    variables already set in the real environment)."""
    global _loaded
    if _loaded:
        return
    path = path or os.path.join(HERE, ".env")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
    _loaded = True


def get(key, default=""):
    load_env()
    return os.environ.get(key, default)


def supabase_config():
    load_env()
    return {
        "url": get("SUPABASE_URL"),
        "ref": get("SUPABASE_REF"),
        "anon": get("SUPABASE_PUBLISHABLE_KEY"),
        "secret": get("SUPABASE_SECRET_KEY"),
    }


def pg_params():
    """Postgres connection params (session pooler) used only for migrations."""
    load_env()
    return {
        "host": get("SUPABASE_DB_HOST"),
        "port": int(get("SUPABASE_DB_PORT", "5432") or 5432),
        "user": get("SUPABASE_DB_USER"),
        "password": get("SUPABASE_DB_PASSWORD"),
        "dbname": get("SUPABASE_DB_NAME", "postgres"),
    }


def use_supabase():
    load_env()
    cfg = supabase_config()
    return get("AUTOMARK_DB", "json").lower() == "supabase" and bool(cfg["url"] and cfg["secret"])


def llm_config():
    load_env()
    provider = get("AUTOMARK_LLM_PROVIDER", "").lower()
    base_urls = {
        "openai": "https://api.openai.com/v1",
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "together": "https://api.together.xyz/v1",
        "ollama": "http://localhost:11434/v1",
    }
    base = get("AUTOMARK_LLM_BASE_URL") or base_urls.get(provider, "")
    return {
        "provider": provider,
        "base_url": base,
        "api_key": get("AUTOMARK_LLM_API_KEY"),
        "model": get("AUTOMARK_LLM_MODEL"),
    }


def llm_enabled():
    c = llm_config()
    if not c["provider"]:
        return False
    if c["provider"] == "ollama":
        return bool(c["base_url"] and c["model"])
    return bool(c["base_url"] and c["api_key"] and c["model"])
