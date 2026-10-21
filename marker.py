"""AutoMark prototype - grading engine.

This implements the SRS grading pipeline in one place:

  Figure 2 (submission grading flow):
      compile -> run approved tests -> style -> rubric comparison -> mark
  Figure 5 (sandbox execution):
      throwaway temp folder, wall-clock watchdog, output truncated
  Figure 6 (mark calculation):
      test_score, rubric_score, style_score -> weighted final mark

It produces the SRS "Contract B" (grader result) and "Contract C" (rubric
result) shapes, then computes the final mark in code -- the AI/heuristic layer
never emits the mark directly (FR-33, FR-37).

Real AutoMark runs student code in a locked-down Docker container (no network,
256 MB, 0.5 CPU, read-only FS). This prototype runs it in a subprocess with a
wall-clock watchdog and output truncation, in an isolated temp folder. That is
NOT a security sandbox -- see README "Security & limitations".
"""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import config
import llm

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- language support ------------------------------------------------------
# Each language says how to "compile" (a syntax/compile check that must never
# crash the grader) and how to run a file. Java works only if a JDK is on PATH.

WALL_CLOCK_SECONDS = 20          # FR-26 watchdog
PER_TEST_SECONDS = 5             # FR-24
MAX_OUTPUT_BYTES = 64 * 1024     # FR-27
STYLE_VIOLATION_CAP = 25         # Figure 6: 1 - violations/25


def _exe(home, name):
    """Return the path to a tool inside a JDK home, or None."""
    for cand in (name + ".exe", name):
        p = os.path.join(home, "bin", cand)
        if os.path.isfile(p):
            return p
    return None


def _find_java_home():
    """Locate a JDK. Order: AUTOMARK_JAVA_HOME, JAVA_HOME, a bundled
    ./jdk/jdk-* folder, then whatever is on PATH. Lets the prototype use a
    portable JDK with no system install or PATH changes (NFR-09)."""
    candidates = []
    for env in ("AUTOMARK_JAVA_HOME", "JAVA_HOME"):
        if os.environ.get(env):
            candidates.append(os.environ[env])
    candidates += sorted(glob.glob(os.path.join(HERE, "jdk", "jdk-*")), reverse=True)
    for home in candidates:
        if _exe(home, "javac") and _exe(home, "java"):
            return home
    return None


_JAVA_HOME = _find_java_home()


def java_tools():
    """Return (javac_path, java_path), falling back to PATH. May be (None, None)."""
    if _JAVA_HOME:
        return _exe(_JAVA_HOME, "javac"), _exe(_JAVA_HOME, "java")
    return shutil.which("javac"), shutil.which("java")


def java_home():
    return _JAVA_HOME


def have_java():
    jc, jv = java_tools()
    return bool(jc and jv)


LANGUAGES = {
    "python": {"ext": ".py", "label": "Python 3"},
    "java": {"ext": ".java", "label": "Java (needs JDK on PATH)"},
}


def run_with_watchdog(cmd, cwd, stdin_text="", timeout=PER_TEST_SECONDS):
    """Run a command with a wall-clock watchdog. Returns (rc, out, err, timed_out, ms)."""
    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as e:
        return 127, "", "tool not found: %s" % e, False, 0
    try:
        out, err = proc.communicate(input=stdin_text, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = "", ""
        timed_out = True
    ms = int((time.time() - start) * 1000)
    out = (out or "")[:MAX_OUTPUT_BYTES]
    err = (err or "")[:MAX_OUTPUT_BYTES]
    return proc.returncode, out, err, timed_out, ms


# ---- compile step (FR-22) --------------------------------------------------

def compile_check(language, workdir, filename):
    """Returns (compiled: bool, errors: [str], run_target: str|None)."""
    path = os.path.join(workdir, filename)
    if language == "python":
        rc, out, err, timed_out, _ = run_with_watchdog(
            [sys.executable, "-I", "-m", "py_compile", path], workdir, timeout=15)
        if rc == 0:
            return True, [], filename
        msg = (err or out).strip().splitlines()
        return False, msg[:20], None
    if language == "java":
        javac, _ = java_tools()
        if not javac:
            return False, ["No JDK found. Install Java 21 (or drop a JDK in the "
                           "project's jdk/ folder) to grade Java submissions."], None
        rc, out, err, timed_out, _ = run_with_watchdog(
            [javac, filename], workdir, timeout=20)
        if rc == 0:
            cls = re.sub(r"\.java$", "", filename)
            return True, [], cls
        return False, (err or out).strip().splitlines()[:20], None
    return False, ["Unsupported language: %s" % language], None


def run_program(language, workdir, run_target, stdin_text, timeout):
    if language == "python":
        return run_with_watchdog(
            [sys.executable, "-I", run_target], workdir, stdin_text, timeout)
    if language == "java":
        _, java = java_tools()
        if not java:
            return 127, "", "no JDK", False, 0
        return run_with_watchdog(
            [java, run_target], workdir, stdin_text, timeout)
    return 127, "", "unsupported language", False, 0


# ---- test runner (FR-23, FR-24) -------------------------------------------

def run_tests(language, workdir, run_target, tests):
    """Run only *approved* I/O tests. Returns Contract-B-style list."""
    results = []
    for t in tests:
        if not t.get("approved"):
            continue
        stdin_text = t.get("stdin", "") or ""
        expected = (t.get("expected", "") or "").strip()
        rc, out, err, timed_out, ms = run_program(
            language, workdir, run_target, stdin_text, PER_TEST_SECONDS)
        actual = (out or "").strip()
        if timed_out:
            passed, message = False, "Timed out after %ss" % PER_TEST_SECONDS
        elif rc not in (0, None) and not actual:
            passed, message = False, "Program error: " + (err.strip().splitlines()[-1] if err.strip() else "non-zero exit")
        else:
            passed = (actual == expected)
            if passed:
                message = None
            else:
                message = "expected %r but got %r" % (
                    _clip(expected, 120), _clip(actual, 120))
        results.append({
            "id": t.get("id"),
            "name": t.get("name", "test"),
            "passed": passed,
            "marks": t.get("marks", 0),
            "message": message,
            "duration_ms": ms,
        })
    return results


def _clip(s, n):
    s = s if s is not None else ""
    return s if len(s) <= n else s[:n] + "..."


# ---- style step (FR-28) ----------------------------------------------------

def style_check(language, code):
    """A lightweight stand-in for Checkstyle: counts simple style issues and
    returns a Contract-B style block with a score in 0..1."""
    lines = code.splitlines()
    violations = []
    for i, line in enumerate(lines, 1):
        if len(line.rstrip("\n")) > 100:
            violations.append("LineTooLong at %d" % i)
        if line.rstrip("\n") != line.rstrip():
            violations.append("TrailingWhitespace at %d" % i)
        if "\t" in line:
            violations.append("TabCharacter at %d" % i)
    if language == "python":
        if not re.search(r'""".*?"""', code, re.S) and not code.strip().startswith("#"):
            violations.append("MissingModuleDocstring")
        for i, line in enumerate(lines, 1):
            m = re.match(r"\s*def\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m and re.search(r"[A-Z]", m.group(1)):
                violations.append("NonSnakeCaseFunction at %d" % i)
    if language == "java":
        if "/**" not in code:
            violations.append("MissingJavadoc")
    count = len(violations)
    score = max(0.0, 1.0 - count / STYLE_VIOLATION_CAP)
    return {"violations": count, "score": round(score, 3), "top_issues": violations[:5]}


# ---- Contract C: rubric comparison ----------------------------------------
# AutoMark uses an LLM here. The prototype ships a deterministic, offline
# heuristic matcher so it works with no API key and no network. It is pluggable:
# set AUTOMARK_LLM and implement llm_compare() to swap in a real model
# (NFR-09, FR-31..FR-33). Because the heuristic only inspects code structure,
# it ignores injected instructions in comments (NFR-05: poisoned == clean).

def _match(patterns, code_lines):
    """Return (number_of_patterns_that_matched, sorted_unique_line_numbers).
    Counts PATTERNS matched, not lines — several patterns may hit one line."""
    count = 0
    lines = []
    for pat in patterns or []:
        for i, line in enumerate(code_lines, 1):
            try:
                hit = re.search(pat, line, re.I)
            except re.error:
                hit = pat.lower() in line.lower()
            if hit:
                count += 1
                lines.append(i)
                break
    return count, sorted(set(lines))


def behaviour_score(contract_b):
    """Fraction of tests that passed, or None if there is nothing to judge by.
    This is the ground-truth signal for 'does the code do the right thing'."""
    tests = contract_b.get("tests") or []
    if not tests:
        return None
    if not contract_b.get("compiled") or contract_b.get("timed_out"):
        return 0.0
    return sum(1 for t in tests if t["passed"]) / len(tests)


def _validate_contract_c(assignment, code, raw, source):
    """FR-32: every rubric id appears exactly once; line numbers lie inside the
    file; an invalid/missing judgement is downgraded to not_met and flagged."""
    n = len(code.splitlines())
    got = {q.get("id"): q for q in (raw.get("requirements") or [])}
    out = []
    for req in assignment.get("rubric", []):
        rid = req["id"]
        q = got.get(rid)
        if not q or q.get("verdict") not in ("met", "partial", "not_met"):
            out.append({"id": rid, "verdict": "not_met", "lines": [],
                        "explanation": "No valid judgement returned; flagged for review.",
                        "needs_review": True, "source": source})
            continue
        lines = [ln for ln in (q.get("lines") or [])
                 if isinstance(ln, int) and 1 <= ln <= n]
        out.append({"id": rid, "verdict": q["verdict"], "lines": lines,
                    "explanation": (q.get("explanation") or "").strip()[:220],
                    "source": source})
    return {"requirements": out}


def _heuristic_contract_c(assignment, code, contract_b):
    """Offline judgement. Prefers BEHAVIOUR: if a requirement lists test_ids, the
    verdict comes from whether those tests passed (so code that is wrong for the
    scenario is caught). Pattern-only requirements get a scenario-fit gate that
    downgrades a structural 'met' when the program fails most of its tests."""
    code_lines = code.splitlines()
    test_pass = {t.get("id"): t["passed"] for t in contract_b.get("tests", []) if t.get("id")}
    runnable = contract_b.get("compiled") and not contract_b.get("timed_out")
    behaviour = behaviour_score(contract_b)
    requirements = []
    for req in assignment.get("rubric", []):
        test_ids = req.get("test_ids") or []
        patterns = req.get("patterns") or []
        _, lines = _match(patterns, code_lines)

        if test_ids:  # behavioural verdict (strongest signal)
            seen = [test_pass[t] for t in test_ids if t in test_pass]
            if not runnable or not seen:
                verdict = "not_met"
                expl = "Could not verify: the program did not compile or run."
                lines = []
            elif all(seen):
                verdict, expl = "met", "Verified by the tests for this requirement."
            elif any(seen):
                verdict, expl = "partial", "Some but not all checks for this requirement passed."
            else:
                verdict = "not_met"
                expl = ("The checks for this requirement failed — the code does "
                        "not do this for the given scenario.")
                lines = []
        elif patterns:  # structural verdict + scenario-fit gate
            hits, lines = _match(patterns, code_lines)
            if hits == len(patterns):
                verdict, expl = "met", "Expected elements are present."
            elif hits:
                verdict, expl = "partial", "%d of %d expected elements found." % (hits, len(patterns))
            else:
                verdict, expl = "not_met", "Expected elements were not found."
            if verdict == "met" and behaviour is not None and behaviour < 0.5:
                verdict = "partial"
                expl += " But the program fails most tests, so it may solve the wrong problem."
        else:
            verdict = "not_met"
            expl = "No automatic check defined; flagged for teacher review."
            lines = []
            requirements.append({"id": req["id"], "verdict": verdict, "lines": lines,
                                 "explanation": expl, "needs_review": True,
                                 "source": "heuristic"})
            continue

        requirements.append({"id": req["id"], "verdict": verdict, "lines": lines,
                             "explanation": expl, "source": "heuristic"})
    return {"requirements": requirements}


def compare_rubric(assignment, code, contract_b):
    """Returns Contract C. Uses an LLM for semantic judgement when configured
    (FR-31), validates it (FR-32), and falls back to the behavioural+heuristic
    engine on any failure (FR-36)."""
    if config.llm_enabled():
        try:
            raw = llm.ask_contract_c(
                assignment.get("brief", ""), assignment.get("rubric", []),
                code, contract_b.get("tests", []))
            return _validate_contract_c(assignment, code, raw, source="llm")
        except Exception:
            pass  # fall back to the offline engine
    return _heuristic_contract_c(assignment, code, contract_b)


# ---- Figure 6: mark calculation -------------------------------------------

VERDICT_VALUE = {"met": 1.0, "partial": 0.5, "not_met": 0.0}


def compute_scores(assignment, contract_b, contract_c):
    # test score
    tests = contract_b["tests"]
    total_marks = sum(t["marks"] for t in tests) or 0
    earned_marks = sum(t["marks"] for t in tests if t["passed"])
    if not contract_b["compiled"] or contract_b["timed_out"]:
        test_score = 0.0  # FR-38
    else:
        test_score = (earned_marks / total_marks) if total_marks else 0.0

    # rubric score (verdict value weighted by each requirement's marks / 100)
    rubric = {r["id"]: r for r in assignment.get("rubric", [])}
    available = sum(r.get("marks", 0) for r in assignment.get("rubric", [])) or 0
    earned = 0.0
    for req in contract_c["requirements"]:
        marks = rubric.get(req["id"], {}).get("marks", 0)
        earned += VERDICT_VALUE.get(req["verdict"], 0.0) * marks
    rubric_score = (earned / available) if available else 0.0

    style_score = contract_b.get("style", {}).get("score", 0.0)

    w = assignment.get("weights", {"tests": 60, "rubric": 25, "style": 15})
    wt, wr, ws = w["tests"] / 100.0, w["rubric"] / 100.0, w["style"] / 100.0
    final = 100.0 * (wt * test_score + wr * rubric_score + ws * style_score)
    return {
        "test_score": round(test_score, 4),
        "rubric_score": round(rubric_score, 4),
        "style_score": round(style_score, 4),
        "final_mark": round(final, 1),
        "weights": w,
    }


# ---- feedback text (FR-34, <=150 words, no model solution) ----------------

def build_feedback(contract_b, contract_c, scores):
    parts = []
    if not contract_b["compiled"]:
        parts.append("Your code did not compile, so the test score is 0. "
                     "Fix the compile errors shown above and resubmit.")
    elif contract_b["timed_out"]:
        parts.append("Your program ran too long and was stopped, so the test "
                     "score is 0. Check for an endless loop.")
    else:
        passed = sum(1 for t in contract_b["tests"] if t["passed"])
        parts.append("You passed %d of %d tests." % (passed, len(contract_b["tests"])))
    met = [r["id"] for r in contract_c["requirements"] if r["verdict"] == "met"]
    partial = [r["id"] for r in contract_c["requirements"] if r["verdict"] == "partial"]
    missing = [r["id"] for r in contract_c["requirements"] if r["verdict"] == "not_met"]
    if met:
        parts.append("Requirements fully met: %s." % ", ".join(met))
    if partial:
        parts.append("Partly met: %s." % ", ".join(partial))
    if missing:
        parts.append("Not met yet: %s. Review the brief for these." % ", ".join(missing))
    style = contract_b.get("style", {})
    if style.get("violations"):
        parts.append("Style: %d issue(s); e.g. %s."
                     % (style["violations"], ", ".join(style.get("top_issues", [])[:2])))
    text = " ".join(parts)
    words = text.split()
    if len(words) > 150:
        text = " ".join(words[:150]) + "..."
    return text


# ---- top-level: grade one submission --------------------------------------

def _public_class_name(code):
    m = re.search(r"public\s+(?:final\s+|abstract\s+)?class\s+([A-Za-z_]\w*)", code)
    if m:
        return m.group(1)
    m = re.search(r"\bclass\s+([A-Za-z_]\w*)", code)
    return m.group(1) if m else None


def target_filename(language, filename, code):
    """Decide what to name the file on disk. For Java the name MUST match the
    public class, so we derive it from the code, not the upload name."""
    if language == "java":
        return (_public_class_name(code) or "Main") + ".java"
    base = os.path.basename(filename or "")
    if not base.endswith(".py"):
        base = (re.sub(r"\.(java|txt)$", "", base) or "Main") + ".py"
    return base


def grade(assignment, filename, code):
    """Run the whole pipeline and return a result dict (status + scores +
    contracts + feedback). Never raises for ordinary student mistakes."""
    language = assignment.get("language", "python")
    stages = {}
    t0 = time.time()
    workdir = tempfile.mkdtemp(prefix="automark_")
    try:
        # write the student file into the throwaway folder (Figure 5 step 1)
        safe_name = target_filename(language, filename, code)
        with open(os.path.join(workdir, safe_name), "w", encoding="utf-8") as f:
            f.write(code)

        # compile (FR-22)
        ts = time.time()
        compiled, errors, run_target = compile_check(language, workdir, safe_name)
        stages["compile_ms"] = int((time.time() - ts) * 1000)

        timed_out = False
        tests = []
        if compiled:
            ts = time.time()
            tests = run_tests(language, workdir, run_target, assignment.get("tests", []))
            stages["tests_ms"] = int((time.time() - ts) * 1000)
            timed_out = any("Timed out" in (t["message"] or "") for t in tests)

        style = style_check(language, code)

        contract_b = {
            "compiled": compiled,
            "compile_errors": errors,
            "timed_out": timed_out,
            "duration_ms": int((time.time() - t0) * 1000),
            "tests": tests,
            "style": style,
        }

        # rubric comparison (Contract C) runs even on compile failure (FR-38)
        ts = time.time()
        contract_c = compare_rubric(assignment, code, contract_b)
        stages["rubric_ms"] = int((time.time() - ts) * 1000)

        scores = compute_scores(assignment, contract_b, contract_c)
        feedback = build_feedback(contract_b, contract_c, scores)

        if config.llm_enabled():
            model = config.llm_config()["provider"] + ":" + config.llm_config()["model"]
        else:
            model = "behavioural+heuristic-v2"
        return {
            "status": "graded",
            "grader": contract_b,       # Contract B
            "rubric": contract_c,       # Contract C
            "scores": scores,
            "feedback": feedback,
            "stages": stages,
            "model": model,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
