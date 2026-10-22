"""Seed a sample published assignment so the prototype works on first run.

Mirrors the SRS acceptance script (section 7): a teacher has turned a brief into
a rubric and approved tests; students can submit. Sample submissions
(correct / compile error / endless loop / wrong output) are included so the
teacher marks table has data to show.
"""

PRIMES_BRIEF = (
    "Write a program that reads a single integer n from standard input and "
    "prints every prime number from 2 up to and including n, one per line, in "
    "increasing order. If n is less than 2, print nothing. A prime number is a "
    "whole number greater than 1 whose only divisors are 1 and itself."
)


PRIMES_GENERATOR = {"count": 4, "stdin": "{n}\n",
                    "inputs": [{"name": "n", "type": "int", "min": 2, "max": 200}]}


def _complete_assignment(store, title, language, rubric, tests, brief=None,
                         reference=None, generator=None):
    """Build a full published assignment and persist it in ONE upsert, so an
    interrupted seed can never leave a half-made (draft, no-tests) assignment."""
    import time
    from store import new_id
    a = {
        "id": new_id("asg_"), "title": title, "brief": brief or PRIMES_BRIEF,
        "language": language, "rubric": rubric, "tests": tests,
        "weights": {"tests": 60, "rubric": 25, "style": 15},
        "status": "published",
        "reference_solution": reference,
        "generator": generator,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return store.put_assignment(a)


PRIMES_TESTS = [
    {"id": "t1", "name": "n = 10", "kind": "io", "stdin": "10\n",
     "expected": "2\n3\n5\n7", "marks": 3, "approved": True},
    {"id": "t2", "name": "n = 2", "kind": "io", "stdin": "2\n",
     "expected": "2", "marks": 2, "approved": True},
    {"id": "t3", "name": "n = 1 (prints nothing)", "kind": "io", "stdin": "1\n",
     "expected": "", "marks": 2, "approved": True},
    {"id": "t4", "name": "n = 20", "kind": "io", "stdin": "20\n",
     "expected": "2\n3\n5\n7\n11\n13\n17\n19", "marks": 3, "approved": True},
    # prime upper bound catches off-by-one (range that excludes n):
    {"id": "t5", "name": "n = 13 (prime)", "kind": "io", "stdin": "13\n",
     "expected": "2\n3\n5\n7\n11\n13", "marks": 3, "approved": True},
]


def _primes_assignment(store):
    rubric = [
        {"id": "R1", "text": "Reads an integer n from standard input.",
         "marks": 25, "patterns": [r"int\s*\(\s*input", r"input\s*\("]},
        {"id": "R2", "text": "Uses a loop to consider candidate numbers.",
         "marks": 25, "patterns": [r"\bfor\b|\bwhile\b"]},
        {"id": "R3", "text": "Tests divisibility to decide primality (correct primes).",
         "marks": 30, "patterns": [r"%", r"\bfor\b|\bwhile\b"], "test_ids": ["t1", "t4", "t5"]},
        {"id": "R4", "text": "Prints each prime on its own line.",
         "marks": 20, "patterns": [r"print\s*\("], "test_ids": ["t1", "t2", "t3", "t4", "t5"]},
    ]
    return _complete_assignment(store, "Prime numbers up to n", "python", rubric,
                                PRIMES_TESTS, reference=GOOD, generator=PRIMES_GENERATOR)


GOOD = """n = int(input())
for x in range(2, n + 1):
    is_prime = True
    for d in range(2, int(x ** 0.5) + 1):
        if x % d == 0:
            is_prime = False
            break
    if is_prime:
        print(x)
"""

COMPILE_ERROR = """n = int(input())
for x in range(2, n + 1)
    print(x)
"""

ENDLESS_LOOP = """n = int(input())
x = 2
while True:
    pass
"""

WRONG_OUTPUT = """n = int(input())
for x in range(2, n + 1):
    print(x)
"""


def _primes_java_assignment(store):
    rubric = [
        {"id": "R1", "text": "Reads an integer n from standard input.",
         "marks": 25, "patterns": [r"new\s+Scanner|BufferedReader", r"nextInt|readLine"]},
        {"id": "R2", "text": "Uses a loop to consider candidate numbers.",
         "marks": 25, "patterns": [r"\bfor\b|\bwhile\b"]},
        {"id": "R3", "text": "Tests divisibility to decide primality (correct primes).",
         "marks": 30, "patterns": [r"%", r"\bfor\b|\bwhile\b"], "test_ids": ["t1", "t4", "t5"]},
        {"id": "R4", "text": "Prints each prime on its own line.",
         "marks": 20, "patterns": [r"System\.out\.print"], "test_ids": ["t1", "t2", "t3", "t4", "t5"]},
    ]
    return _complete_assignment(store, "Prime numbers up to n (Java)", "java",
                                rubric, PRIMES_TESTS, reference=GOOD_JAVA,
                                generator=PRIMES_GENERATOR)


GOOD_JAVA = """import java.util.Scanner;

public class PrimeNumbers {
    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);
        int n = input.nextInt();
        for (int number = 2; number <= n; number++) {
            boolean isPrime = true;
            for (int divisor = 2; divisor <= number / divisor; divisor++) {
                if (number % divisor == 0) {
                    isPrime = false;
                    break;
                }
            }
            if (isPrime) {
                System.out.println(number);
            }
        }
        input.close();
    }
}
"""


JUNIT_BRIEF = (
    "Write a class named Primes with a public static method "
    "`boolean isPrime(int n)` that returns true if n is a prime number and "
    "false otherwise (numbers below 2 are not prime). Your method is checked by "
    "automated JUnit tests."
)

JUNIT_TEST_SRC = """import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class PrimesTest {
    @Test void detectsPrimes() {
        assertTrue(Primes.isPrime(2));
        assertTrue(Primes.isPrime(7));
        assertTrue(Primes.isPrime(13));
        assertTrue(Primes.isPrime(97));
    }
    @Test void detectsNonPrimes() {
        assertFalse(Primes.isPrime(1));
        assertFalse(Primes.isPrime(9));
        assertFalse(Primes.isPrime(100));
    }
    @Test void handlesEdges() {
        assertFalse(Primes.isPrime(0));
        assertFalse(Primes.isPrime(-5));
    }
}
"""

GOOD_JUNIT_SOLUTION = """public class Primes {
    public static boolean isPrime(int n) {
        if (n < 2) return false;
        for (int d = 2; (long) d * d <= n; d++) {
            if (n % d == 0) return false;
        }
        return true;
    }
}
"""


def _junit_assignment(store):
    rubric = [
        {"id": "R1", "text": "Defines isPrime(int).", "marks": 20, "patterns": [r"isPrime"]},
        {"id": "R2", "text": "Correctly identifies primes and non-primes.",
         "marks": 60, "patterns": [r"%|/"], "test_ids": ["j1"]},
        {"id": "R3", "text": "Code is well-exercised by the tests (no dead code).",
         "marks": 20, "coverage_min": 80},
    ]
    tests = [
        {"id": "j1", "name": "isPrime JUnit suite", "kind": "junit",
         "marks": 10, "approved": True, "body": JUNIT_TEST_SRC},
    ]
    return _complete_assignment(store, "Prime checker (JUnit)", "java", rubric,
                                tests, brief=JUNIT_BRIEF)


# Demo accounts (password is the same for all demo users so you can sign in).
DEMO_PASSWORD = "password123"
DEMO_USERS = [
    ("teacher@automark.test", "teacher", "Prof. Ada"),
    ("alice@class.test", "student", "Alice"),
    ("bob@class.test", "student", "Bob"),
    ("carol@class.test", "student", "Carol"),
    ("dave@class.test", "student", "Dave"),
    ("erin@class.test", "student", "Erin"),
]


def _seed_users(store):
    import auth
    for email, role, name in DEMO_USERS:
        if not store.get_user_by_email(email):
            store.create_user(email, auth.hash_password(DEMO_PASSWORD),
                              role=role, display_name=name)


def seed(store, mark_fn):
    """Create demo accounts, sample assignments, and graded submissions.
    Users and assignments are seeded independently so accounts still appear even
    if the assignment tables were populated on an earlier run."""
    _seed_users(store)
    if store.list_assignments():
        return  # assignments already seeded
    a = _primes_assignment(store)
    samples = [
        ("alice@class.test", "Primes.py", GOOD),
        ("bob@class.test", "Primes.py", COMPILE_ERROR),
        ("carol@class.test", "Primes.py", ENDLESS_LOOP),
        ("dave@class.test", "Primes.py", WRONG_OUTPUT),
    ]
    for student, fname, code in samples:
        sub = store.create_submission(a["id"], student, fname, code)
        mark_fn(sub)

    # Java sample (only seed a graded submission if a JDK is available)
    import marker
    ja = _primes_java_assignment(store)
    if marker.have_java():
        sub = store.create_submission(ja["id"], "erin@class.test",
                                      "PrimeNumbers.java", GOOD_JAVA)
        mark_fn(sub)
        # JUnit-based assignment + a correct sample submission
        ju = _junit_assignment(store)
        sub = store.create_submission(ju["id"], "erin@class.test",
                                      "Primes.java", GOOD_JUNIT_SOLUTION)
        mark_fn(sub)
