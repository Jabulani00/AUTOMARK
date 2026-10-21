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


def _primes_assignment(store):
    a = store.create_assignment("Prime numbers up to n", PRIMES_BRIEF, language="python")
    a["rubric"] = [
        {"id": "R1", "text": "Reads an integer n from standard input.",
         "marks": 25, "patterns": [r"int\s*\(\s*input", r"input\s*\("]},
        {"id": "R2", "text": "Uses a loop to consider candidate numbers.",
         "marks": 25, "patterns": [r"\bfor\b|\bwhile\b"]},
        {"id": "R3", "text": "Tests divisibility to decide primality (correct primes).",
         "marks": 30, "patterns": [r"%", r"\bfor\b|\bwhile\b"], "test_ids": ["t1", "t4"]},
        {"id": "R4", "text": "Prints each prime on its own line.",
         "marks": 20, "patterns": [r"print\s*\("], "test_ids": ["t1", "t2", "t3", "t4"]},
    ]
    a["tests"] = [
        {"id": "t1", "name": "n = 10", "kind": "io", "stdin": "10\n",
         "expected": "2\n3\n5\n7", "marks": 3, "approved": True},
        {"id": "t2", "name": "n = 2", "kind": "io", "stdin": "2\n",
         "expected": "2", "marks": 2, "approved": True},
        {"id": "t3", "name": "n = 1 (prints nothing)", "kind": "io", "stdin": "1\n",
         "expected": "", "marks": 2, "approved": True},
        {"id": "t4", "name": "n = 20", "kind": "io", "stdin": "20\n",
         "expected": "2\n3\n5\n7\n11\n13\n17\n19", "marks": 3, "approved": True},
    ]
    a["status"] = "published"
    store.put_assignment(a)
    return a


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
    a = store.create_assignment("Prime numbers up to n (Java)", PRIMES_BRIEF,
                                language="java")
    a["rubric"] = [
        {"id": "R1", "text": "Reads an integer n from standard input.",
         "marks": 25, "patterns": [r"new\s+Scanner|BufferedReader", r"nextInt|readLine"]},
        {"id": "R2", "text": "Uses a loop to consider candidate numbers.",
         "marks": 25, "patterns": [r"\bfor\b|\bwhile\b"]},
        {"id": "R3", "text": "Tests divisibility to decide primality (correct primes).",
         "marks": 30, "patterns": [r"%", r"\bfor\b|\bwhile\b"], "test_ids": ["t1", "t4"]},
        {"id": "R4", "text": "Prints each prime on its own line.",
         "marks": 20, "patterns": [r"System\.out\.print"], "test_ids": ["t1", "t2", "t3", "t4"]},
    ]
    a["tests"] = [
        {"id": "t1", "name": "n = 10", "kind": "io", "stdin": "10\n",
         "expected": "2\n3\n5\n7", "marks": 3, "approved": True},
        {"id": "t2", "name": "n = 2", "kind": "io", "stdin": "2\n",
         "expected": "2", "marks": 2, "approved": True},
        {"id": "t3", "name": "n = 1 (prints nothing)", "kind": "io", "stdin": "1\n",
         "expected": "", "marks": 2, "approved": True},
        {"id": "t4", "name": "n = 20", "kind": "io", "stdin": "20\n",
         "expected": "2\n3\n5\n7\n11\n13\n17\n19", "marks": 3, "approved": True},
    ]
    a["status"] = "published"
    store.put_assignment(a)
    return a


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
