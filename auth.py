"""Authentication for the AutoMark prototype (standard library only).

- Passwords: PBKDF2-HMAC-SHA256 with a per-user salt (never stored in plaintext).
- Sessions: a stateless signed token  user_id.timestamp.hmac  verified with the
  server's AUTOMARK_SESSION_SECRET. No token means "not signed in".

The full product would use Supabase Auth (GoTrue). This keeps the prototype
self-contained while still being safe (hashed passwords, signed sessions).
"""

import base64
import hashlib
import hmac
import os
import time

import config

_PBKDF2_ROUNDS = 120_000
_SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days


def _secret():
    s = config.get("AUTOMARK_SESSION_SECRET")
    return (s or "dev-insecure-secret").encode("utf-8")


# ---- passwords ------------------------------------------------------------
def hash_password(password):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return "pbkdf2_sha256$%d$%s$%s" % (
        _PBKDF2_ROUNDS, salt.hex(), dk.hex())


def verify_password(password, stored):
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---- session tokens -------------------------------------------------------
def _sign(msg):
    return hmac.new(_secret(), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def make_token(user_id):
    issued = str(int(time.time()))
    payload = "%s.%s" % (user_id, issued)
    return "%s.%s" % (payload, _sign(payload))


def parse_token(token):
    """Return user_id if the token is valid and unexpired, else None."""
    if not token:
        return None
    try:
        user_id, issued, sig = token.rsplit(".", 2)
    except ValueError:
        return None
    payload = "%s.%s" % (user_id, issued)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        if time.time() - int(issued) > _SESSION_MAX_AGE:
            return None
    except ValueError:
        return None
    return user_id
