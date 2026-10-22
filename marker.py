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
import hashlib
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

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


# ---- multi-file submissions ------------------------------------------------
# A submission may contain several files, each introduced by a marker line
#   // FILE: Helper.java      (Java)       # FILE: helper.py   (Python)
# Otherwise it is a single file named after its public class / upload name.
FILE_MARKER = re.compile(r"^[ \t]*(?://|#)[ \t]*FILE:[ \t]*(\S+)[ \t]*$", re.M)


def split_sources(language, code, filename):
    marks = list(FILE_MARKER.finditer(code or ""))
    if not marks:
        return [(target_filename(language, filename, code), code or "")]
    files = []
    for i, m in enumerate(marks):
        name = os.path.basename(m.group(1).strip())
        end = marks[i + 1].start() if i + 1 < len(marks) else len(code)
        files.append((name, code[m.end():end].strip("\n") + "\n"))
    return files


def _find_main_class(names, codes):
    for nm, c in zip(names, codes):
        if re.search(r"static\s+void\s+main\s*\(", c):
            return _public_class_name(c) or re.sub(r"\.java$", "", nm)
    return _public_class_name(codes[0]) or re.sub(r"\.java$", "", names[0])


def compile_sources(language, workdir, names, codes):
    """Compile one or more student files. Returns
    (compiled, errors, run_target, warnings). For Java, javac -Xlint:all
    surfaces compiler warnings (FR-22) which feed the style score."""
    if language == "python":
        for nm in names:
            rc, out, err, to, _ = run_with_watchdog(
                [sys.executable, "-I", "-m", "py_compile", nm], workdir, timeout=15)
            if rc != 0:
                return False, (err or out).strip().splitlines()[:20], None, []
        return True, [], names[0], []
    if language == "java":
        javac, _ = java_tools()
        if not javac:
            return False, ["No JDK found (install Java 21 or drop a JDK in jdk/)."], None, []
        rc, out, err, to, _ = run_with_watchdog(
            [javac, "-Xlint:all"] + names, workdir, timeout=40)
        txt = (err or "") + "\n" + (out or "")
        warnings = [re.sub(r"^.*?\.java:", "", ln).strip()
                    for ln in txt.splitlines() if "warning:" in ln][:10]
        if rc != 0:
            errs = [ln for ln in txt.strip().splitlines()
                    if "error:" in ln or ln.startswith(" ")][:20]
            return False, (errs or txt.strip().splitlines())[:20], None, warnings
        return True, [], _find_main_class(names, codes), warnings
    return False, ["Unsupported language: %s" % language], None, []


# JVM resource caps for untrusted student code (SRS FR-25: 256 MB). A memory hog
# then dies with OutOfMemoryError instead of exhausting the host.
JAVA_LIMITS = ["-Xmx256m", "-Xss16m", "-XX:+UseSerialGC"]


# ---- optional Docker sandbox (SRS FR-25: real network/file isolation) ------
# When AUTOMARK_SANDBOX=docker and the Docker daemon is reachable, the untrusted
# student program is executed INSIDE a throwaway container with no network, a
# read-only root filesystem (only the mounted job folder is writable), dropped
# capabilities and memory/pid/cpu caps. Falls back to the subprocess runner when
# Docker is unavailable, so the app always works. Build the image once:
#     docker build -t automark-grader grader/
_DOCKER_OK = None


def docker_available():
    global _DOCKER_OK
    if _DOCKER_OK is None:
        _DOCKER_OK = False
        if shutil.which("docker"):
            try:
                p = subprocess.run(["docker", "info"], capture_output=True, timeout=12)
                _DOCKER_OK = (p.returncode == 0)
            except Exception:
                _DOCKER_OK = False
    return _DOCKER_OK


def sandbox_image():
    return config.get("AUTOMARK_SANDBOX_IMAGE", "automark-grader")


def sandbox_enabled():
    return config.get("AUTOMARK_SANDBOX", "none").lower() == "docker" and docker_available()


def _run_sandboxed(workdir, inner_cmd, stdin_text, timeout):
    """Run inner_cmd inside a locked-down container over the mounted workdir."""
    name = "automark_" + os.urandom(6).hex()
    docker = ["docker", "run", "--rm", "--name", name,
              "--network", "none", "--read-only",
              "--memory", "256m", "--memory-swap", "256m", "--cpus", "0.5",
              "--pids-limit", "64", "--cap-drop", "ALL",
              "--security-opt", "no-new-privileges", "-i",
              "-v", "%s:/work" % workdir, "-w", "/work",
              sandbox_image()] + inner_cmd
    start = time.time()
    try:
        proc = subprocess.Popen(docker, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as e:
        return 127, "", "docker not found: %s" % e, False, 0
    try:
        out, err = proc.communicate(input=stdin_text, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        proc.kill()
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = "", ""
        timed_out = True
    ms = int((time.time() - start) * 1000)
    return proc.returncode, (out or "")[:MAX_OUTPUT_BYTES], (err or "")[:MAX_OUTPUT_BYTES], timed_out, ms


def run_program(language, workdir, run_target, stdin_text, timeout):
    if language not in ("python", "java"):
        return 127, "", "unsupported language", False, 0
    java = None
    if language == "java":
        _, java = java_tools()
        if not java:
            return 127, "", "no JDK", False, 0
    if sandbox_enabled():
        # inside the container java/python3 are on PATH
        inner = (["python3", "-I", run_target] if language == "python"
                 else ["java"] + JAVA_LIMITS + [run_target])
        return _run_sandboxed(workdir, inner, stdin_text, timeout)
    # host fallback: use the resolved interpreter / JDK paths
    inner = ([sys.executable, "-I", run_target] if language == "python"
             else [java] + JAVA_LIMITS + [run_target])
    return run_with_watchdog(inner, workdir, stdin_text, timeout)


# ---- test runner (FR-23, FR-24) -------------------------------------------

def _extract_numbers(s):
    return [float(x) for x in re.findall(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?", s or "")]


def _error_lines(language, errors):
    """Pull the 1-based line numbers out of compiler error text, for UI
    highlighting of the offending lines."""
    nums = set()
    pat = r"\.java:(\d+):" if language == "java" else r"line (\d+)"
    for e in errors or []:
        m = re.search(pat, e or "")
        if m:
            nums.add(int(m.group(1)))
    return sorted(nums)


def _judge(t, out, rc, err, timed_out):
    """Decide pass/fail + a message for one test. Supports exact (normalised)
    and numeric (tolerance) comparison."""
    if timed_out:
        return False, "Timed out after %ss" % PER_TEST_SECONDS
    expected = _normalize_output(t.get("expected", ""))
    actual = _normalize_output(out)
    if rc not in (0, None) and not actual:
        tail = err.strip().splitlines()[-1] if err.strip() else "non-zero exit"
        return False, "Program error: " + tail
    if t.get("compare") == "numeric":
        tol = float(t.get("tolerance", 1e-6))
        en, an = _extract_numbers(expected), _extract_numbers(actual)
        if not en or len(en) != len(an):
            return False, "expected %d number(s) but produced %d" % (len(en), len(an))
        for e, a in zip(en, an):
            if abs(e - a) > tol + 1e-12:
                return False, "expected ~%s (±%s) but got %s" % (
                    _clip(str(en), 80), tol, _clip(str(an), 80))
        return True, None
    if actual == expected:
        return True, None
    return False, "expected %r but got %r" % (_clip(expected, 120), _clip(actual, 120))


def run_tests(language, workdir, run_target, tests):
    """Run only *approved* tests. Returns Contract-B-style list. A test marked
    hidden never reveals its input/expected output in the result message."""
    results = []
    for t in tests:
        if not t.get("approved") or t.get("kind") == "junit":
            continue  # junit tests are handled separately (run_junit_tests)
        stdin_text = t.get("stdin", "") or ""
        rc, out, err, timed_out, ms = run_program(
            language, workdir, run_target, stdin_text, PER_TEST_SECONDS)
        passed, message = _judge(t, out, rc, err, timed_out)
        if t.get("hidden") and not passed:
            message = "hidden check not passed (input and expected output are hidden)"
        results.append({
            "id": t.get("id"),
            "name": t.get("name", "test"),
            "passed": passed,
            "marks": t.get("marks", 0),
            "message": message,
            "duration_ms": ms,
            "hidden": bool(t.get("hidden")),
        })
    return results


# ---- JUnit test runner (Java, FR-23) --------------------------------------
# A junit test case carries `body` (a JUnit 5 test class source). We compile it
# against the student's already-compiled class and run the JUnit console
# launcher, then parse the pass/fail summary.

def junit_jar():
    cands = glob.glob(os.path.join(HERE, "lib", "junit-platform-console-standalone*.jar"))
    return cands[0] if cands else None


def _junit_class_name(body):
    m = (re.search(r"public\s+class\s+([A-Za-z_]\w*)", body)
         or re.search(r"\bclass\s+([A-Za-z_]\w*)", body))
    return m.group(1) if m else None


def jacoco_jars():
    agent = os.path.join(HERE, "lib", "jacocoagent.jar")
    cli = os.path.join(HERE, "lib", "jacococli.jar")
    return (agent, cli) if os.path.isfile(agent) and os.path.isfile(cli) else (None, None)


def _parse_junit_xml(reports_dir):
    """Per-method results from the JUnit legacy XML report."""
    methods = []
    for fn in glob.glob(os.path.join(reports_dir, "*.xml")):
        try:
            tree = ET.parse(fn)
        except Exception:
            continue
        for tc in tree.iter("testcase"):
            fe = tc.find("failure")
            fe = fe if fe is not None else tc.find("error")
            methods.append({
                "method": re.sub(r"\(\)$", "", tc.get("name", "")),
                "passed": fe is None,
                "message": _clip((fe.get("message") or "").strip(), 120) if fe is not None else None,
            })
    return methods


def _jacoco_coverage(workdir, exec_file, exclude_classes):
    """Return student-code line coverage % from a jacoco.exec, or None."""
    _, cli = jacoco_jars()
    _, java = java_tools()
    if not cli or not java or not os.path.isfile(os.path.join(workdir, exec_file)):
        return None
    csv_rel = "jacoco.csv"
    rc, out, err, to, _ = run_with_watchdog(
        [java, "-jar", cli, "report", exec_file, "--classfiles", ".",
         "--csv", csv_rel], workdir, timeout=25)
    csv_path = os.path.join(workdir, csv_rel)
    if not os.path.isfile(csv_path):
        return None
    covered = missed = 0
    import csv as _csv
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in _csv.DictReader(f):
            if row.get("CLASS") in exclude_classes:
                continue
            missed += int(row.get("LINE_MISSED", 0) or 0)
            covered += int(row.get("LINE_COVERED", 0) or 0)
    total = covered + missed
    return round(100 * covered / total) if total else None


def run_junit_tests(workdir, junit_tests):
    jar = junit_jar()
    javac, java = java_tools()
    results = []
    if not jar or not javac or not java:
        return results
    sep = ";" if os.name == "nt" else ":"
    cp = jar + sep + "."
    agent, _cli = jacoco_jars()
    for t in junit_tests:
        if not t.get("approved"):
            continue
        body = t.get("body", "") or ""
        cls = _junit_class_name(body)
        base = {"id": t.get("id"), "name": t.get("name", "junit"),
                "marks": t.get("marks", 0), "hidden": False}
        if not cls:
            results.append({**base, "passed": False, "message": "no test class found"})
            continue
        with open(os.path.join(workdir, cls + ".java"), "w", encoding="utf-8") as f:
            f.write(body)
        rc, out, err, to, ms = run_with_watchdog([javac, "-cp", cp, cls + ".java"],
                                                 workdir, timeout=25)
        if rc != 0:
            tail = (err or out).strip().splitlines()[-1:] or ["compile error"]
            results.append({**base, "passed": False,
                            "message": "test did not compile: " + tail[0][:100]})
            continue
        reldir = "reports_" + (t.get("id") or cls)
        execf = "jacoco_%s.exec" % (t.get("id") or cls)
        jvm = ["-Xmx256m"]
        if agent:
            jvm.append("-javaagent:%s=destfile=%s" % (agent, execf))
        rc, out, err, to, ms = run_with_watchdog(
            [java] + jvm + ["-jar", jar, "execute", "--class-path", ".",
             "--select-class", cls, "--reports-dir", reldir, "--disable-banner"],
            workdir, timeout=WALL_CLOCK_SECONDS)
        methods = _parse_junit_xml(os.path.join(workdir, reldir))
        if methods:
            succ = sum(1 for m in methods if m["passed"])
            total = len(methods)
        else:  # fall back to the text summary
            txt = (out or "") + "\n" + (err or "")
            s = re.search(r"(\d+) tests successful", txt)
            fo = re.search(r"(\d+) tests failed", txt)
            succ = int(s.group(1)) if s else 0
            total = succ + (int(fo.group(1)) if fo else 0)
        passed = total > 0 and succ == total
        earned = round(base["marks"] * succ / total) if total else 0
        coverage = _jacoco_coverage(workdir, execf, exclude_classes={cls}) if agent else None
        results.append({**base, "passed": passed, "duration_ms": ms, "earned": earned,
                        "methods": methods, "coverage": coverage,
                        "message": None if passed
                        else "%d of %d JUnit checks passed" % (succ, total)})
    return results


# ---- randomized hidden tests (anti-gaming) --------------------------------
# A teacher can attach a trusted reference_solution plus a generator spec. At
# grade time we sample random inputs (seeded by the student's code, so a given
# submission is reproducible — NFR-02 — but the inputs are unknowable in
# advance), run the reference to get the expected output, then run the student
# against them. Hard-coding the known samples cannot pass these.

def _run_reference(language, ref_code, stdins):
    """Compile the reference once, run it per input. Returns expected outputs
    (normalised), or None if the reference does not compile."""
    workdir = tempfile.mkdtemp(prefix="automark_ref_")
    try:
        name = target_filename(language, "Reference", ref_code)
        with open(os.path.join(workdir, name), "w", encoding="utf-8") as f:
            f.write(ref_code)
        compiled, errors, run_target = compile_check(language, workdir, name)
        if not compiled:
            return None
        outs = []
        for s in stdins:
            rc, out, err, to, ms = run_program(
                language, workdir, run_target, s, PER_TEST_SECONDS)
            outs.append(None if to else _normalize_output(out))
        return outs
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def generate_hidden_tests(assignment, code):
    gen = assignment.get("generator")
    ref = assignment.get("reference_solution")
    if not gen or not ref:
        return []
    count = int(gen.get("count", 4))
    template = gen.get("stdin", "{n}\n")
    specs = gen.get("inputs", [{"name": "n", "type": "int", "min": 2, "max": 200}])
    # deterministic per submission, but unpredictable without the exact code
    seed = int(hashlib.sha256((code or "").encode("utf-8")).hexdigest()[:12], 16)
    rng = random.Random(seed)
    stdins = []
    for _ in range(count):
        vals = {}
        for sp in specs:
            if sp.get("type") == "float":
                vals[sp["name"]] = round(rng.uniform(sp.get("min", 0), sp.get("max", 100)),
                                         sp.get("places", 2))
            else:
                vals[sp["name"]] = rng.randint(sp.get("min", 0), sp.get("max", 100))
        stdins.append(template.format(**vals))
    expected = _run_reference(assignment.get("language", "python"), ref, stdins)
    if expected is None:
        return []
    tests = []
    for i, (s, e) in enumerate(zip(stdins, expected), 1):
        if e is None:
            continue
        t = {"id": "hidden_%d" % i, "name": "hidden check #%d" % i, "kind": "io",
             "stdin": s, "expected": e, "marks": gen.get("marks_each", 3),
             "approved": True, "hidden": True}
        if gen.get("compare"):
            t["compare"] = gen["compare"]
        if gen.get("tolerance") is not None:
            t["tolerance"] = gen["tolerance"]
        tests.append(t)
    return tests


def _clip(s, n):
    s = s if s is not None else ""
    return s if len(s) <= n else s[:n] + "..."


def _normalize_output(s):
    """Fair output comparison: unify newlines, strip trailing whitespace on each
    line, and drop trailing blank lines (SRS 'compare trimmed stdout'). This
    keeps cosmetic differences (a stray trailing space, CRLF) from failing an
    otherwise-correct answer."""
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in s.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def detect_hardcoding(code, approved_tests):
    """Anti-gaming: spot submissions that embed the tests' expected outputs as
    string literals (hard-coding answers for known inputs) instead of computing
    them. Returns (hits, [test names]). A real solution computes its output and
    will not contain two or more full expected outputs verbatim."""
    hits, names = 0, []
    for t in approved_tests:
        exp = (t.get("expected") or "").strip()
        if len(exp) < 2:            # ignore empty/trivial expectations
            continue
        needles = {exp, exp.replace("\n", "\\n"), exp.replace("\n", "\\r\\n")}
        if any(nd and nd in code for nd in needles):
            hits += 1
            names.append(t.get("name"))
    return hits, names


# ---- style step (FR-28) ----------------------------------------------------

def checkstyle_jar():
    cands = glob.glob(os.path.join(HERE, "lib", "checkstyle-*.jar"))
    return cands[0] if cands else None


def _checkstyle(code):
    """Run real Checkstyle on Java code (FR-28). Returns a list of issue strings,
    or None if Checkstyle/JDK/ruleset is unavailable (caller falls back)."""
    jar = checkstyle_jar()
    _, java = java_tools()
    ruleset = os.path.join(HERE, "grader", "checkstyle.xml")
    if not jar or not java or not os.path.isfile(ruleset):
        return None
    workdir = tempfile.mkdtemp(prefix="automark_cs_")
    try:
        name = (_public_class_name(code) or "Main") + ".java"
        with open(os.path.join(workdir, name), "w", encoding="utf-8") as f:
            f.write(code)
        rc, out, err, to, ms = run_with_watchdog(
            [java, "-jar", jar, "-c", ruleset, name], workdir, timeout=30)
        if to:
            return None
        issues = []
        for line in ((out or "") + "\n" + (err or "")).splitlines():
            m = re.match(r"\[(?:WARN|ERROR)\]\s*(.*)", line)
            if m:
                issues.append(re.sub(r"^.*?\.java:", "", m.group(1)).strip())
        return issues
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def style_check(language, code):
    """Contract-B style block with a score in 0..1. Java uses real Checkstyle
    when available (FR-28); otherwise a lightweight hand-rolled check is used."""
    if language == "java":
        cs = _checkstyle(code)
        if cs is not None:
            count = len(cs)
            return {"violations": count,
                    "score": round(max(0.0, 1.0 - count / STYLE_VIOLATION_CAP), 3),
                    "top_issues": cs[:5], "tool": "checkstyle"}
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

        cmin = req.get("coverage_min")
        if cmin:  # verdict from measured test coverage (JaCoCo)
            covs = [t["coverage"] for t in contract_b.get("tests", [])
                    if t.get("coverage") is not None]
            cov = max(covs) if covs else None
            if cov is None:
                verdict, expl = "not_met", "Code coverage could not be measured."
            elif cov >= cmin:
                verdict, expl = "met", "Coverage %d%% meets the %d%% target." % (cov, cmin)
            elif cov >= 0.7 * cmin:
                verdict, expl = "partial", "Coverage %d%% is below the %d%% target." % (cov, cmin)
            else:
                verdict, expl = "not_met", "Coverage %d%% is well below the %d%% target." % (cov, cmin)
            requirements.append({"id": req["id"], "verdict": verdict, "lines": [],
                                 "explanation": expl, "source": "coverage"})
            continue

        if test_ids:  # behavioural verdict (strongest signal)
            seen = [test_pass[t] for t in test_ids if t in test_pass]
            # correctness requirements also count the randomized hidden checks,
            # so passing only the known samples (gaming) cannot earn 'met'
            seen += [t["passed"] for t in contract_b.get("tests", []) if t.get("hidden")]
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
    # a test may report partial 'earned' marks (e.g. JUnit: some methods passed)
    earned_marks = sum(t.get("earned", t["marks"] if t["passed"] else 0) for t in tests)
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

def build_feedback(assignment, contract_b, contract_c, scores):
    """Specific, actionable feedback (<=150 words, no model solution, FR-34)."""
    parts = []
    all_tests = contract_b["tests"]
    visible = [t for t in all_tests if not t.get("hidden")]
    hidden = [t for t in all_tests if t.get("hidden")]
    rubric_text = {r["id"]: r.get("text", "") for r in assignment.get("rubric", [])}

    if not contract_b["compiled"]:
        err = (contract_b.get("compile_errors") or [""])[-1]
        parts.append("Your code did not compile, so the test score is 0. "
                     "Fix the error (%s) and resubmit." % _clip(err.strip(), 80))
    elif contract_b["timed_out"]:
        parts.append("Your program ran too long and was stopped (test score 0). "
                     "Check for an endless loop or missing input read.")
    else:
        vp = sum(1 for t in visible if t["passed"])
        parts.append("You passed %d of %d sample tests." % (vp, len(visible)))
        failing = [t["name"] for t in visible if not t["passed"]][:4]
        if failing:
            parts.append("Failing: %s." % ", ".join(failing))
        if hidden:
            hp = sum(1 for t in hidden if t["passed"])
            parts.append("Hidden checks (random inputs): passed %d of %d%s."
                         % (hp, len(hidden),
                            "" if hp == len(hidden) else " — your logic is not fully correct"))

    missing = [r for r in contract_c["requirements"] if r["verdict"] == "not_met"]
    partial = [r for r in contract_c["requirements"] if r["verdict"] == "partial"]
    if missing:
        ids = ", ".join("%s (%s)" % (r["id"], rubric_text.get(r["id"], "").rstrip("."))
                        for r in missing[:3])
        parts.append("Not met: %s. Re-read the brief for these." % ids)
    if partial:
        parts.append("Partly met: %s." % ", ".join(r["id"] for r in partial))

    style = contract_b.get("style", {})
    if style.get("violations"):
        parts.append("Style: %d issue(s), e.g. %s."
                     % (style["violations"], ", ".join(style.get("top_issues", [])[:2])))
    text = " ".join(parts)
    words = text.split()
    return " ".join(words[:150]) + ("..." if len(words) > 150 else "")


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
        # write the student file(s) into the throwaway folder (Figure 5 step 1).
        # A multi-file submission uses "// FILE: Name.java" markers.
        sources = split_sources(language, code, filename)
        names, codes = [], []
        for nm, content in sources:
            with open(os.path.join(workdir, nm), "w", encoding="utf-8") as f:
                f.write(content)
            names.append(nm)
            codes.append(content)

        # compile (FR-22) — javac -Xlint:all also surfaces warnings
        ts = time.time()
        compiled, errors, run_target, warnings = compile_sources(
            language, workdir, names, codes)
        stages["compile_ms"] = int((time.time() - ts) * 1000)

        timed_out = False
        tests = []
        if compiled:
            ts = time.time()
            tests = run_tests(language, workdir, run_target, assignment.get("tests", []))
            # Randomized hidden checks the student can't hard-code for — only
            # worth running (and only affordable, timeout-wise) when the visible
            # samples mostly pass, which is exactly the correct/gaming case.
            # JUnit tests (Java): compile teacher test classes against the student
            if language == "java":
                junit_ts = [t for t in assignment.get("tests", [])
                            if t.get("approved") and t.get("kind") == "junit"]
                if junit_ts:
                    tests += run_junit_tests(workdir, junit_ts)
            vis_pass = (sum(1 for t in tests if t["passed"]) / len(tests)) if tests else 0
            if vis_pass >= 0.6:
                hidden = generate_hidden_tests(assignment, code)
                if hidden:
                    tests += run_tests(language, workdir, run_target, hidden)
            stages["tests_ms"] = int((time.time() - ts) * 1000)
            timed_out = any("Timed out" in (t["message"] or "") for t in tests)

        style = style_check(language, code)
        # fold javac -Xlint warnings into the style signal (FR-22/FR-28)
        if warnings:
            style["violations"] = style.get("violations", 0) + len(warnings)
            style["top_issues"] = (style.get("top_issues", [])
                                   + ["warning: " + w for w in warnings])[:6]
            style["score"] = round(max(0.0, 1.0 - style["violations"] / STYLE_VIOLATION_CAP), 3)

        contract_b = {
            "compiled": compiled,
            "compile_errors": errors,
            "error_lines": _error_lines(language, errors),
            "timed_out": timed_out,
            "warnings": warnings,
            "duration_ms": int((time.time() - t0) * 1000),
            "tests": tests,
            "style": style,
        }

        # rubric comparison (Contract C) runs even on compile failure (FR-38)
        ts = time.time()
        contract_c = compare_rubric(assignment, code, contract_b)
        stages["rubric_ms"] = int((time.time() - ts) * 1000)

        scores = compute_scores(assignment, contract_b, contract_c)

        # anti-gaming (two layers):
        flags = []
        approved = [t for t in assignment.get("tests", []) if t.get("approved")]
        # 1) literal: embeds the known tests' expected outputs as strings
        hc_hits, hc_names = detect_hardcoding(code, approved)
        if compiled and hc_hits >= 2:
            flags.append("possible_hardcoded_output")
            scores["final_mark"] = min(scores["final_mark"], 40.0)
            scores["capped"] = True
        # 2) behavioural: passes the visible samples but fails the randomized
        #    hidden checks (catches lookup tables / overfitting of any form)
        vis = [t for t in tests if not t.get("hidden")]
        hid = [t for t in tests if t.get("hidden")]
        if compiled and len(hid) >= 3 and vis:
            vr = sum(1 for t in vis if t["passed"]) / len(vis)
            hr = sum(1 for t in hid if t["passed"]) / len(hid)
            if vr >= 0.8 and hr <= 0.5:
                flags.append("overfit_to_sample_tests")
                scores["final_mark"] = min(scores["final_mark"], 45.0)
                scores["capped"] = True

        feedback = build_feedback(assignment, contract_b, contract_c, scores)
        if "possible_hardcoded_output" in flags:
            feedback = ("Flagged for teacher review: this code appears to print "
                        "the expected answers for the known test inputs rather "
                        "than computing them, so the mark is capped. ") + feedback

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
            "flags": flags,
            "stages": stages,
            "model": model,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
