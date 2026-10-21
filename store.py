"""AutoMark prototype - JSON-file data store.

A tiny persistence layer so the prototype needs no database server.
Everything lives in data/db.json and can be rebuilt by deleting that file
(the server reseeds a sample assignment on first run). This mirrors the SRS
entities (profiles, assignments, test_cases, submissions, results) in a single
document, which is enough for a single-machine prototype.
"""

import json
import os
import threading
import time
import uuid

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "db.json")

_lock = threading.RLock()


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_id(prefix=""):
    return prefix + uuid.uuid4().hex[:12]


_DEFAULT = {
    "assignments": {},   # id -> assignment
    "submissions": {},   # id -> submission (includes the result once graded)
    "users": {},         # id -> user (email, password_hash, role, display_name)
}


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load():
    with _lock:
        if not os.path.exists(DB_PATH):
            return json.loads(json.dumps(_DEFAULT))
        with open(DB_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return json.loads(json.dumps(_DEFAULT))


def save(db):
    with _lock:
        _ensure_dir()
        tmp = DB_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2)
        os.replace(tmp, DB_PATH)


class Store:
    """Thin convenience wrapper around the JSON document."""

    def __init__(self):
        self.db = load()

    def _commit(self):
        save(self.db)

    # ---- assignments ---------------------------------------------------
    def list_assignments(self, published_only=False):
        items = list(self.db["assignments"].values())
        if published_only:
            items = [a for a in items if a.get("status") == "published"]
        items.sort(key=lambda a: a.get("created_at", ""), reverse=True)
        return items

    def get_assignment(self, aid):
        return self.db["assignments"].get(aid)

    def put_assignment(self, assignment):
        self.db["assignments"][assignment["id"]] = assignment
        self._commit()
        return assignment

    def create_assignment(self, title, brief, language="python"):
        aid = new_id("asg_")
        assignment = {
            "id": aid,
            "title": title,
            "brief": brief,
            "language": language,
            "rubric": [],          # [{id, text, marks, patterns:[...]}]
            "tests": [],           # [{id, name, kind, stdin, expected, marks, approved}]
            "weights": {"tests": 60, "rubric": 25, "style": 15},
            "status": "draft",     # draft | published
            "created_at": _now(),
        }
        return self.put_assignment(assignment)

    def delete_assignment(self, aid):
        self.db["assignments"].pop(aid, None)
        # drop submissions for it too
        for sid in [s for s, v in self.db["submissions"].items()
                    if v.get("assignment_id") == aid]:
            self.db["submissions"].pop(sid, None)
        self._commit()

    # ---- submissions ---------------------------------------------------
    def create_submission(self, assignment_id, student, filename, code):
        sid = new_id("sub_")
        existing = [s for s in self.db["submissions"].values()
                    if s.get("assignment_id") == assignment_id
                    and s.get("student") == student]
        sub = {
            "id": sid,
            "assignment_id": assignment_id,
            "student": student,
            "filename": filename,
            "code": code,
            "status": "pending",       # pending|running|graded|failed
            "attempts": len(existing) + 1,
            "created_at": _now(),
            "result": None,
            "error": None,
            "override": None,          # {mark, reason, at}
        }
        self.db["submissions"][sid] = sub
        self._commit()
        return sub

    def get_submission(self, sid):
        return self.db["submissions"].get(sid)

    def update_submission(self, sub):
        self.db["submissions"][sub["id"]] = sub
        self._commit()
        return sub

    def list_submissions(self, assignment_id=None, student=None):
        items = list(self.db["submissions"].values())
        if assignment_id:
            items = [s for s in items if s.get("assignment_id") == assignment_id]
        if student:
            items = [s for s in items if s.get("student") == student]
        items.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        return items

    # ---- users ---------------------------------------------------------
    def create_user(self, email, password_hash, role="student", display_name=None):
        self.db.setdefault("users", {})
        uid = new_id("usr_")
        user = {
            "id": uid, "email": email.lower(), "password_hash": password_hash,
            "role": role, "display_name": display_name or email.split("@")[0],
            "created_at": _now(),
        }
        self.db["users"][uid] = user
        self._commit()
        return user

    def get_user_by_email(self, email):
        for u in self.db.get("users", {}).values():
            if u.get("email") == email.lower():
                return u
        return None

    def get_user(self, uid):
        return self.db.get("users", {}).get(uid)

    def list_users(self):
        return list(self.db.get("users", {}).values())
