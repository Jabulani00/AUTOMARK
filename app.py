"""AutoMark prototype - web server (pure Python standard library).

Run:  python app.py           (then open http://localhost:8000)

Serves a single-page UI (web/index.html) and a small JSON API. Grading runs
synchronously on submit for the prototype; the real system uses a polling
worker (FR-17..FR-21). No external packages are required.
"""

import csv
import io
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import analytics
import auth
import config
import marker
import seed as seed_module
from store import Store

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
PORT = int(os.environ.get("AUTOMARK_PORT", "8000"))

DB_BACKEND = "json"


def _make_store():
    """Use Supabase when configured and reachable; otherwise the local JSON store."""
    global DB_BACKEND
    config.load_env()
    if config.use_supabase():
        try:
            from db import SupabaseStore
            s = SupabaseStore()
            s.ping()  # fails if the tables do not exist yet
            DB_BACKEND = "supabase"
            print("  Data backend: Supabase (%s)" % config.supabase_config()["ref"],
                  flush=True)
            return s
        except Exception as e:
            print("  Supabase selected but unavailable (%s)\n"
                  "  -> run supabase/migrations/0001_init.sql, then restart. "
                  "Using local JSON for now." % e, flush=True)
    return Store()


store = _make_store()
_grade_lock = threading.Lock()


def grade_submission(sub):
    """Run the pipeline for one submission and persist the result.
    Mirrors the status lifecycle pending -> running -> graded/failed (Figure 4)."""
    with _grade_lock:
        assignment = store.get_assignment(sub["assignment_id"])
        if not assignment:
            sub["status"] = "failed"
            sub["error"] = "Assignment not found."
            return store.update_submission(sub)
        sub["status"] = "running"
        store.update_submission(sub)
        try:
            result = marker.grade(assignment, sub["filename"], sub["code"])
            sub["result"] = result
            sub["status"] = "graded"
            sub["error"] = None
        except Exception as e:  # grader itself crashed (should be rare)
            sub["status"] = "failed"
            sub["error"] = "Grader error: %s" % e
        return store.update_submission(sub)


def displayed_mark(sub):
    if sub.get("override"):
        return sub["override"]["mark"]
    if sub.get("result"):
        return sub["result"]["scores"]["final_mark"]
    return None


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "AutoMark/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[automark] " + (fmt % args) + "\n")

    # ---- helpers ----
    def _send_json(self, obj, status=200, cookies=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for c in (cookies or []):
            self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(body)

    # ---- auth helpers ----
    def _cookies(self):
        raw = self.headers.get("Cookie", "")
        jar = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                jar[k] = v
        return jar

    def _current_user(self):
        token = self._cookies().get("automark_session")
        uid = auth.parse_token(token)
        if not uid:
            return None
        return store.get_user(uid)

    def _public_user(self, user):
        return {"id": user["id"], "email": user["email"],
                "role": user["role"], "display_name": user.get("display_name")}

    @staticmethod
    def _session_cookie(token, clear=False):
        if clear:
            return "automark_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        return ("automark_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
                % (token, 7 * 24 * 3600))

    def _send_text(self, text, status=200, ctype="text/plain; charset=utf-8",
                   extra_headers=None):
        body = text.encode("utf-8") if isinstance(text, str) else text
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return {}

    def _serve_file(self, path):
        full = os.path.join(WEB_DIR, path)
        if not os.path.isfile(full):
            self._send_text("Not found", 404)
            return
        ctype = "text/html; charset=utf-8"
        if full.endswith(".js"):
            ctype = "text/javascript; charset=utf-8"
        elif full.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        with open(full, "rb") as f:
            self._send_text(f.read(), 200, ctype)

    # ---- GET ----
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        q = parse_qs(u.query)
        if p == "/" or p == "/index.html":
            return self._serve_file("index.html")
        if p.startswith("/web/"):
            return self._serve_file(p[len("/web/"):])
        if p == "/api/env":
            return self._send_json({
                "java": marker.have_java(),
                "languages": marker.LANGUAGES,
                "python": sys.version.split()[0],
                "db": DB_BACKEND,
                "llm": config.llm_config()["provider"] or "heuristic",
            })
        if p == "/api/auth/me":
            u = self._current_user()
            return self._send_json({"user": self._public_user(u) if u else None})
        if p == "/api/analytics":
            u = self._current_user()
            if not u or u["role"] != "teacher":
                return self._send_json({"error": "teacher only"}, 403)
            return self._send_json(analytics.compute(store))
        if p == "/api/assignments":
            u = self._current_user()
            is_teacher = bool(u and u["role"] == "teacher")
            return self._send_json(
                {"assignments": store.list_assignments(published_only=not is_teacher)})
        if p.startswith("/api/assignments/") and p.endswith("/export.csv"):
            aid = p.split("/")[3]
            return self._export_csv(aid)
        if p.startswith("/api/assignments/"):
            aid = p.split("/")[3]
            a = store.get_assignment(aid)
            if not a:
                return self._send_json({"error": "not found"}, 404)
            return self._send_json(a)
        if p == "/api/submissions":
            u = self._current_user()
            if not u:
                return self._send_json({"error": "login required"}, 401)
            aid = q.get("assignment_id", [None])[0]
            student = None if u["role"] == "teacher" else u["email"]
            subs = store.list_submissions(aid, student)
            return self._send_json({"submissions": [self._sub_summary(s) for s in subs]})
        if p.startswith("/api/submission/"):
            sid = p.split("/")[3]
            u = self._current_user()
            s = store.get_submission(sid)
            if not s:
                return self._send_json({"error": "not found"}, 404)
            if not u or (u["role"] != "teacher" and s.get("student") != u["email"]):
                return self._send_json({"error": "forbidden"}, 403)
            return self._send_json(s)
        return self._send_text("Not found", 404)

    def _sub_summary(self, s):
        return {
            "id": s["id"], "student": s["student"], "filename": s["filename"],
            "assignment_id": s.get("assignment_id"),
            "status": s["status"], "attempts": s["attempts"],
            "created_at": s["created_at"],
            "mark": displayed_mark(s),
            "overridden": bool(s.get("override")),
            "original_mark": (s["result"]["scores"]["final_mark"]
                              if s.get("result") else None),
        }

    # ---- POST / PUT / DELETE ----
    def do_POST(self):
        p = urlparse(self.path).path
        body = self._read_json()

        # ---- auth (no session required) ----
        if p == "/api/auth/signup":
            return self._handle_signup(body)
        if p == "/api/auth/login":
            return self._handle_login(body)
        if p == "/api/auth/logout":
            return self._send_json({"ok": True},
                                   cookies=[self._session_cookie("", clear=True)])

        # ---- student: submit (any logged-in user) ----
        if p == "/api/submit":
            return self._handle_submit(body)

        # ---- everything below is teacher-only ----
        me = self._current_user()
        if not me or me["role"] != "teacher":
            return self._send_json({"error": "teacher only"}, 403)

        if p == "/api/assignments":
            a = store.create_assignment(
                body.get("title", "Untitled"), body.get("brief", ""),
                body.get("language", "python"))
            return self._send_json(a, 201)

        parts = p.strip("/").split("/")
        if len(parts) == 4 and parts[1] == "assignments":
            aid, action = parts[2], parts[3]
            a = store.get_assignment(aid)
            if not a:
                return self._send_json({"error": "not found"}, 404)
            if action == "publish":
                return self._handle_publish(a)
            if action == "autodraft":
                return self._handle_autodraft(a)

        if len(parts) == 4 and parts[1] == "submission":
            sid, action = parts[2], parts[3]
            s = store.get_submission(sid)
            if not s:
                return self._send_json({"error": "not found"}, 404)
            if action == "override":
                mark = body.get("mark")
                reason = body.get("reason", "")
                if mark is None or not reason.strip():
                    return self._send_json({"error": "mark and reason required"}, 400)
                s["override"] = {"mark": float(mark), "reason": reason,
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                store.update_submission(s)
                return self._send_json(self._sub_summary(s))
            if action == "regrade":
                grade_submission(s)
                return self._send_json(store.get_submission(sid))

        return self._send_json({"error": "unknown endpoint"}, 404)

    # ---- auth handlers ----
    def _handle_signup(self, body):
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""
        name = (body.get("display_name") or "").strip()
        if "@" not in email or len(password) < 6:
            return self._send_json(
                {"error": "A valid email and a password of 6+ characters are required."}, 400)
        if store.get_user_by_email(email):
            return self._send_json({"error": "That email is already registered."}, 409)
        # Self sign-up always creates a student (FR-02: teachers are invite-only).
        user = store.create_user(email, auth.hash_password(password),
                                 role="student", display_name=name or None)
        token = auth.make_token(user["id"])
        return self._send_json({"user": self._public_user(user)}, 201,
                               cookies=[self._session_cookie(token)])

    def _handle_login(self, body):
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""
        user = store.get_user_by_email(email)
        if not user or not auth.verify_password(password, user["password_hash"]):
            return self._send_json({"error": "Wrong email or password."}, 401)
        token = auth.make_token(user["id"])
        return self._send_json({"user": self._public_user(user)},
                               cookies=[self._session_cookie(token)])

    def do_PUT(self):
        me = self._current_user()
        if not me or me["role"] != "teacher":
            return self._send_json({"error": "teacher only"}, 403)
        parts = urlparse(self.path).path.strip("/").split("/")
        body = self._read_json()
        if len(parts) == 3 and parts[1] == "assignments":
            aid = parts[2]
            a = store.get_assignment(aid)
            if not a:
                return self._send_json({"error": "not found"}, 404)
            for field in ("title", "brief", "rubric", "tests", "weights", "language"):
                if field in body:
                    a[field] = body[field]
            store.put_assignment(a)
            return self._send_json(a)
        return self._send_json({"error": "unknown endpoint"}, 404)

    def do_DELETE(self):
        me = self._current_user()
        if not me or me["role"] != "teacher":
            return self._send_json({"error": "teacher only"}, 403)
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) == 3 and parts[1] == "assignments":
            store.delete_assignment(parts[2])
            return self._send_json({"ok": True})
        return self._send_json({"error": "unknown endpoint"}, 404)

    # ---- handlers ----
    def _handle_submit(self, body):
        me = self._current_user()
        if not me:
            return self._send_json({"error": "Please sign in to submit."}, 401)
        aid = body.get("assignment_id")
        code = body.get("code", "")
        student = me["email"]  # submissions are tied to the signed-in account
        filename = body.get("filename") or "Submission"
        a = store.get_assignment(aid)
        if not a or a.get("status") != "published":
            return self._send_json({"error": "Assignment not found or not published."}, 400)
        # FR-12/FR-13: validate size and non-empty before storing
        if not code.strip():
            return self._send_json({"error": "Empty submission rejected."}, 400)
        if len(code.encode("utf-8")) > 100 * 1024:
            return self._send_json({"error": "File too large (max 100 KB)."}, 400)
        sub = store.create_submission(aid, student, filename, code)
        grade_submission(sub)
        return self._send_json(store.get_submission(sub["id"]), 201)

    def _handle_publish(self, a):
        # FR-10: publish only if every test is approved and there is >= 1 test
        tests = a.get("tests", [])
        if not tests:
            return self._send_json({"error": "Add at least one test before publishing."}, 400)
        if any(not t.get("approved") for t in tests):
            return self._send_json({"error": "Every test must be approved before publishing."}, 400)
        total = sum(r.get("marks", 0) for r in a.get("rubric", []))
        if total != 100:
            return self._send_json(
                {"error": "Rubric marks must total 100 (currently %s)." % total}, 400)
        w = a.get("weights", {})
        if sum(w.get(k, 0) for k in ("tests", "rubric", "style")) != 100:
            return self._send_json({"error": "Weights must sum to 100."}, 400)
        a["status"] = "published"
        store.put_assignment(a)
        return self._send_json(a)

    def _handle_autodraft(self, a):
        """FR-06/FR-08 analog: draft rubric + tests from the brief heuristically."""
        brief = a.get("brief", "")
        if not a.get("rubric"):
            a["rubric"] = [
                {"id": "R1", "text": "Reads the required input.", "marks": 30,
                 "patterns": [r"input\s*\("]},
                {"id": "R2", "text": "Implements the core logic with a loop/condition.",
                 "marks": 45, "patterns": [r"\bfor\b|\bwhile\b", r"\bif\b"]},
                {"id": "R3", "text": "Prints the expected output.", "marks": 25,
                 "patterns": [r"print\s*\("]},
            ]
        if not a.get("tests"):
            a["tests"] = [
                {"id": "t1", "name": "example", "kind": "io", "stdin": "",
                 "expected": "", "marks": 1, "approved": False},
            ]
        store.put_assignment(a)
        return self._send_json(a)

    def _export_csv(self, aid):
        a = store.get_assignment(aid)
        if not a:
            return self._send_text("not found", 404)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["student", "filename", "status", "attempts",
                    "final_mark", "override_mark", "reason", "created_at"])
        for s in store.list_submissions(aid):
            res = s.get("result") or {}
            scores = res.get("scores") or {}
            ov = s.get("override") or {}
            w.writerow([s["student"], s["filename"], s["status"], s["attempts"],
                        scores.get("final_mark", ""), ov.get("mark", ""),
                        ov.get("reason", ""), s["created_at"]])
        fname = "marks_%s.csv" % aid
        self._send_text(buf.getvalue(), 200, "text/csv; charset=utf-8",
                        {"Content-Disposition": 'attachment; filename="%s"' % fname})


def main():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = "http://localhost:%d" % PORT
    print("AutoMark prototype running at %s" % url, flush=True)
    print("  Java toolchain detected: %s" % marker.have_java(), flush=True)
    print("  Seeding sample data in the background (takes ~20s for the "
          "endless-loop demo submission)…", flush=True)
    print("  Press Ctrl+C to stop.", flush=True)
    # Seed after the server is up so grading the slow demo submissions
    # (the endless-loop sample hits the 20s watchdog) never blocks startup.
    threading.Thread(target=seed_module.seed, args=(store, grade_submission),
                     daemon=True).start()
    try:
        if os.environ.get("AUTOMARK_OPEN", "1") == "1":
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
