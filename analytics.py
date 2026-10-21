"""Analytics for the teacher dashboard — computed from stored submissions.

Everything here is derived data (no new storage): averages, pass rate, grade
distribution, and which requirements/tests students fail most. Works with either
the JSON or the Supabase store (it only calls list_* methods).
"""

PASS_MARK = 50.0
BANDS = [("A (80-100)", 80, 101), ("B (70-79)", 70, 80), ("C (60-69)", 60, 70),
         ("D (50-59)", 50, 60), ("F (0-49)", 0, 50)]


def _displayed_mark(sub):
    if sub.get("override"):
        return sub["override"].get("mark")
    res = sub.get("result") or {}
    return (res.get("scores") or {}).get("final_mark")


def _assignment_stats(assignment, subs):
    graded = [s for s in subs if s.get("status") == "graded" and _displayed_mark(s) is not None]
    marks = [_displayed_mark(s) for s in graded]
    n = len(marks)

    def avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    test_scores, rubric_scores, style_scores = [], [], []
    req_not_met = {}
    test_fail = {}
    for s in graded:
        res = s.get("result") or {}
        sc = res.get("scores") or {}
        if "test_score" in sc:
            test_scores.append(sc["test_score"] * 100)
            rubric_scores.append(sc["rubric_score"] * 100)
            style_scores.append(sc["style_score"] * 100)
        for q in (res.get("rubric") or {}).get("requirements", []):
            if q.get("verdict") == "not_met":
                req_not_met[q["id"]] = req_not_met.get(q["id"], 0) + 1
        for t in (res.get("grader") or {}).get("tests", []):
            if not t.get("passed"):
                test_fail[t.get("name", "?")] = test_fail.get(t.get("name", "?"), 0) + 1

    distribution = []
    for label, lo, hi in BANDS:
        distribution.append({"band": label, "count": sum(1 for m in marks if lo <= m < hi)})

    passed = sum(1 for m in marks if m >= PASS_MARK)
    return {
        "id": assignment["id"],
        "title": assignment["title"],
        "language": assignment.get("language"),
        "status": assignment.get("status"),
        "submissions": len(subs),
        "graded": n,
        "students": len({s.get("student") for s in subs}),
        "avg_mark": avg(marks),
        "pass_rate": round(100 * passed / n) if n else None,
        "avg_test": avg(test_scores),
        "avg_rubric": avg(rubric_scores),
        "avg_style": avg(style_scores),
        "distribution": distribution,
        "hardest_requirements": sorted(
            [{"id": k, "not_met": v} for k, v in req_not_met.items()],
            key=lambda x: -x["not_met"])[:4],
        "hardest_tests": sorted(
            [{"name": k, "fails": v} for k, v in test_fail.items()],
            key=lambda x: -x["fails"])[:4],
    }


def compute(store):
    assignments = store.list_assignments()
    per = []
    all_subs = []
    for a in assignments:
        subs = store.list_submissions(assignment_id=a["id"])
        all_subs.extend(subs)
        per.append(_assignment_stats(a, subs))

    graded = [s for s in all_subs if s.get("status") == "graded" and _displayed_mark(s) is not None]
    marks = [_displayed_mark(s) for s in graded]
    totals = {
        "assignments": len(assignments),
        "submissions": len(all_subs),
        "graded": len(graded),
        "students": len({s.get("student") for s in all_subs}),
        "avg_mark": round(sum(marks) / len(marks), 1) if marks else None,
        "pass_rate": round(100 * sum(1 for m in marks if m >= PASS_MARK) / len(marks)) if marks else None,
    }
    per.sort(key=lambda x: (x["submissions"] == 0, x["title"]))
    return {"totals": totals, "assignments": per}
