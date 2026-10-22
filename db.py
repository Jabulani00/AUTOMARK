"""Supabase (PostgREST) data adapter — stdlib urllib only.

Implements the same interface as store.Store, backed by the Supabase Data API
using the SECRET (service-role) key. Selected at startup when AUTOMARK_DB=supabase
and the tables exist. The Python server is the trusted worker here (SRS: the
worker uses the service key), so it bypasses RLS.
"""

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config
from store import new_id

_TRANSIENT = (http.client.IncompleteRead, http.client.RemoteDisconnected,
              ConnectionError, TimeoutError)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class SupabaseError(RuntimeError):
    pass


class SupabaseStore:
    def __init__(self):
        cfg = config.supabase_config()
        self.base = cfg["url"].rstrip("/") + "/rest/v1/"
        self.key = cfg["secret"]
        if not self.base or not self.key:
            raise SupabaseError("Supabase URL/secret not configured")

    # ---- low-level REST ----
    def _headers(self, prefer=None):
        h = {
            "apikey": self.key,
            "Authorization": "Bearer " + self.key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",  # avoid gzip (urllib won't decode it)
        }
        if prefer:
            h["Prefer"] = prefer
        return h

    def _request(self, method, table, query="", body=None, prefer=None, retries=3):
        url = self.base + table + (("?" + query) if query else "")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        last = None
        for attempt in range(retries):
            req = urllib.request.Request(url, data=data, method=method,
                                         headers=self._headers(prefer))
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read()
                    return json.loads(raw.decode("utf-8")) if raw else None
            except urllib.error.HTTPError as e:
                msg = e.read().decode("utf-8", "ignore")
                raise SupabaseError("%s %s -> %s %s" % (method, table, e.code, msg))
            except _TRANSIENT as e:
                last = e
                time.sleep(0.4 * (attempt + 1))  # transient read; back off and retry
        raise SupabaseError("%s %s -> transient read failed after %d tries: %s"
                            % (method, table, retries, last))

    def ping(self):
        """Confirm the tables exist (raises on failure)."""
        self._request("GET", "assignments", "select=id&limit=1")
        return True

    # ---- assignments ---------------------------------------------------
    def list_assignments(self, published_only=False):
        q = "select=*&order=created_at.desc"
        if published_only:
            q += "&status=eq.published"
        return self._request("GET", "assignments", q) or []

    def get_assignment(self, aid):
        rows = self._request("GET", "assignments",
                             "select=*&id=eq.%s&limit=1" % urllib.parse.quote(aid))
        return rows[0] if rows else None

    def put_assignment(self, assignment):
        rows = self._request("POST", "assignments", body=assignment,
                             prefer="resolution=merge-duplicates,return=representation")
        return rows[0] if rows else assignment

    def create_assignment(self, title, brief, language="python"):
        assignment = {
            "id": new_id("asg_"), "title": title, "brief": brief,
            "language": language, "rubric": [], "tests": [],
            "weights": {"tests": 60, "rubric": 25, "style": 15},
            "status": "draft", "created_at": _now(),
        }
        return self.put_assignment(assignment)

    def delete_assignment(self, aid):
        self._request("DELETE", "assignments", "id=eq.%s" % urllib.parse.quote(aid))

    # ---- submissions ---------------------------------------------------
    def create_submission(self, assignment_id, student, filename, code):
        existing = self._request(
            "GET", "submissions",
            "select=id&assignment_id=eq.%s&student=eq.%s"
            % (urllib.parse.quote(assignment_id), urllib.parse.quote(student))) or []
        sub = {
            "id": new_id("sub_"), "assignment_id": assignment_id, "student": student,
            "filename": filename, "code": code, "status": "pending",
            "attempts": len(existing) + 1, "created_at": _now(),
            "result": None, "error": None, "override": None,
        }
        rows = self._request("POST", "submissions", body=sub,
                             prefer="return=representation")
        return rows[0] if rows else sub

    def get_submission(self, sid):
        rows = self._request("GET", "submissions",
                             "select=*&id=eq.%s&limit=1" % urllib.parse.quote(sid))
        return rows[0] if rows else None

    def update_submission(self, sub):
        patch = {k: sub[k] for k in
                 ("status", "attempts", "result", "error", "override")
                 if k in sub}
        rows = self._request("PATCH", "submissions",
                             "id=eq.%s" % urllib.parse.quote(sub["id"]),
                             body=patch, prefer="return=representation")
        return rows[0] if rows else sub

    def list_submissions(self, assignment_id=None, student=None):
        q = "select=*&order=created_at.desc"
        if assignment_id:
            q += "&assignment_id=eq.%s" % urllib.parse.quote(assignment_id)
        if student:
            q += "&student=eq.%s" % urllib.parse.quote(student)
        return self._request("GET", "submissions", q) or []

    # ---- users ---------------------------------------------------------
    def create_user(self, email, password_hash, role="student", display_name=None):
        user = {
            "id": new_id("usr_"), "email": email.lower(),
            "password_hash": password_hash, "role": role,
            "display_name": display_name or email.split("@")[0],
            "created_at": _now(),
        }
        rows = self._request("POST", "app_users", body=user,
                             prefer="return=representation")
        return rows[0] if rows else user

    def get_user_by_email(self, email):
        rows = self._request("GET", "app_users",
                             "select=*&email=eq.%s&limit=1"
                             % urllib.parse.quote(email.lower()))
        return rows[0] if rows else None

    def get_user(self, uid):
        rows = self._request("GET", "app_users",
                             "select=*&id=eq.%s&limit=1" % urllib.parse.quote(uid))
        return rows[0] if rows else None

    def list_users(self):
        return self._request("GET", "app_users", "select=*&order=created_at.desc") or []
