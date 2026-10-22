"""Hostile-program pack for the Java grader (SRS NFR-03 / AC-03).

Runs 10 hostile Java programs through marker.grade() and checks that:
  * the grader ALWAYS returns a result and never crashes or hangs past the
    watchdog (the core safety property we can guarantee on any host), and
  * the programs that this prototype is designed to contain (endless loop,
    fork bomb, memory hog, endless output) are indeed stopped.

It also documents, honestly, what this prototype does NOT sandbox (network,
host file reads, disk writes) — those need the real Docker sandbox from the SRS.
Run:  python tests/hostile_pack.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import marker  # noqa: E402

ASSIGNMENT = {
    "language": "java", "weights": {"tests": 60, "rubric": 25, "style": 15},
    "rubric": [{"id": "R1", "text": "runs", "marks": 100, "patterns": [r"main"]}],
    "tests": [{"id": "t1", "name": "run", "kind": "io", "stdin": "",
               "expected": "__never_matches__", "marks": 1, "approved": True}],
}

# (name, expectation, java source).  expectation ∈ {contain, handle, not_blocked}
HOSTILE = [
    ("endless_loop", "contain", """public class Hostile {
    public static void main(String[] a) { while (true) { } }
}"""),
    ("fork_bomb", "contain", """public class Hostile {
    public static void main(String[] a) {
        while (true) { new Thread(() -> { while (true) { } }).start(); }
    }
}"""),
    ("memory_hog", "contain", """public class Hostile {
    public static void main(String[] a) {
        java.util.List<long[]> keep = new java.util.ArrayList<>();
        while (true) { keep.add(new long[10_000_000]); }
    }
}"""),
    ("endless_output", "contain", """public class Hostile {
    public static void main(String[] a) { while (true) { System.out.println("x"); } }
}"""),
    ("early_exit", "handle", """public class Hostile {
    public static void main(String[] a) { System.exit(0); }
}"""),
    ("compile_error", "handle", """public class Hostile {
    public static void main(String[] a) { this is not java }
}"""),
    ("correct", "handle", """public class Hostile {
    public static void main(String[] a) { System.out.println("ok"); }
}"""),
    ("network_call", "not_blocked", """import java.net.*;
public class Hostile {
    public static void main(String[] a) {
        try (Socket s = new Socket()) {
            s.connect(new InetSocketAddress("10.255.255.1", 80), 1200);
            System.out.println("connected");
        } catch (Exception e) { System.out.println("net-failed"); }
    }
}"""),
    ("host_file_read", "not_blocked", """import java.io.*;
public class Hostile {
    public static void main(String[] a) {
        String[] list = new File(System.getProperty("user.home")).list();
        System.out.println("read-home:" + (list == null ? 0 : list.length));
    }
}"""),
    ("huge_file_write", "not_blocked", """import java.io.*;
public class Hostile {
    public static void main(String[] a) throws Exception {
        File f = File.createTempFile("automark_hostile", ".tmp");
        try (FileOutputStream o = new FileOutputStream(f)) {
            byte[] chunk = new byte[1024 * 1024];
            for (int i = 0; i < 5; i++) o.write(chunk);
        }
        System.out.println("wrote:" + f.length());
        f.delete();
    }
}"""),
]


def run():
    if not marker.have_java():
        print("No JDK — cannot run the hostile pack.")
        return False
    ok = True
    print("%-16s %-12s %-9s %s" % ("program", "expectation", "verdict", "detail"))
    for name, expect, code in HOSTILE:
        t0 = time.time()
        try:
            r = marker.grade(ASSIGNMENT, "Hostile.java", code)
        except Exception as e:  # grader must NEVER crash on hostile input
            print("%-16s %-12s FAIL      grader crashed: %s" % (name, expect, e))
            ok = False
            continue
        elapsed = time.time() - t0
        cb = r["grader"]
        tmsg = (cb["tests"][0]["message"] if cb["tests"] else "") or ""
        contained = cb["timed_out"] or ("OutOfMemory" in tmsg) or ("Program error" in tmsg)
        survived = r["status"] == "graded" and elapsed < 60

        if expect == "contain":
            verdict = "CONTAINED" if (contained and survived) else "LEAKED"
            ok = ok and contained and survived
            detail = "timed_out=%s %s (%.1fs)" % (cb["timed_out"], tmsg[:40], elapsed)
        elif expect == "handle":
            verdict = "OK" if survived else "FAIL"
            ok = ok and survived
            detail = "compiled=%s (%.1fs)" % (cb["compiled"], elapsed)
        else:  # not_blocked — we only require the grader to survive
            verdict = "SURVIVED" if survived else "FAIL"
            ok = ok and survived
            detail = "not sandboxed here: %s (%.1fs)" % (tmsg[:30] or "ran", elapsed)
        print("%-16s %-12s %-9s %s" % (name, expect, verdict, detail))

    print("\nHostile pack:", "PASS" if ok else "FAIL",
          "— grader survived all 10; contained loop/fork/memory/output.")
    print("NOTE: network, host-file-read and disk-write are NOT blocked in this "
          "prototype (no container). See README 'Security & limitations'.")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
