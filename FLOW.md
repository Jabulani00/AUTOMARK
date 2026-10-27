# AutoMark — flow & navigation guide

How to move around the app and do everything as a **Lecturer (teacher)** or a
**Student**. Diagrams render on GitHub / VS Code preview.

- App URL: <http://localhost:8000> (start with `python app.py`)
- Demo logins (password **`password123`**):
  - Lecturer → `teacher@automark.test`
  - Students → `alice@class.test` … `erin@class.test`

---

## 1. Who does what

Everyone lands on the **sign-in** screen. Your **role decides which screens you
see** — the app routes you automatically after login.

```mermaid
flowchart TD
    A(["Open http://localhost:8000"]) --> B[Sign-in screen]
    B -->|email + password| C{Role?}
    C -->|teacher| T[👩‍🏫 Lecturer area]
    C -->|student| S[🎓 Student area]

    T --> T1[Create &amp; edit assignments]
    T --> T2[Add rubric + tests, approve, publish]
    T --> T3[Class marks: override, regrade, export CSV]
    T --> T4[Analytics dashboard]

    S --> S1[Pick a published assignment]
    S --> S2[Upload / write / zip a program]
    S --> S3[Submit &amp; get a mark + feedback]
    S --> S4[My submissions history]

    classDef t fill:#1e3a8a,stroke:#3b82f6,color:#fff
    classDef s fill:#065f46,stroke:#10b981,color:#fff
    class T,T1,T2,T3,T4 t
    class S,S1,S2,S3,S4 s
```

**Sign in / sign up**

- On the landing page, the card has two tabs: **Sign in** and **Create student
  account**.
- *Create student account* self-registers a **student** only (lecturer accounts
  are created by seeding/invite — SRS FR-02).
- After login the top bar shows your **avatar, name, role** and a **Sign out**
  button. Students never see the lecturer screens, and vice-versa.

---

## 2. Screen map

```mermaid
flowchart LR
    L[Sign-in] --> SV[Student view]
    L --> TV[Teacher view]

    subgraph SV[🎓 Student view]
      direction TB
      SA[Pick an assignment] --- SB[Upload or write program]
      SB --- SR[Result: gauge, breakdown, feedback]
      SR --- SM[My submissions]
    end

    subgraph TV[👩‍🏫 Teacher view]
      direction TB
      TN{{"Nav: Assignments & Marks · Analytics"}}
      TN --- TE[Assignment editor]
      TE --- TR[Rubric table]
      TR --- TT[Test cases: I/O or JUnit]
      TT --- TM[Class marks]
      TN --- TAN[Analytics dashboard]
    end
```

---

## 3. Student flow

### Step by step

1. **Sign in** (or *Create student account*).
2. **Pick an assignment** (left card) — choose from the **Assignment** dropdown;
   the **brief** appears below it. Only *published* assignments are listed.
3. **Provide your program** (right card), any one of:
   - **Write it** — type/paste into the **Code** box, or
   - **Upload a file** — *Choose File* → a `.py` / `.java` / `.txt`, or
   - **Upload a `.zip`** of several source files (multi-file projects).
4. Click **Submit for marking →**.
5. **Read your result** (appears below): the big **mark gauge**, the **Tests /
   Rubric / Style** score bars, **Feedback**, the **test breakdown**, and
   **requirement verdicts**. If your code didn't compile, the offending line is
   highlighted.
6. **Resubmit** anytime — just change the code and submit again. Every attempt is
   kept and listed under **My submissions** (click a row to reopen its result).

```mermaid
flowchart TD
    A[Sign in] --> B[Pick an assignment]
    B --> C[Read the brief]
    C --> D{Provide program}
    D -->|type / paste| E[Code box]
    D -->|upload file| E
    D -->|upload .zip| E
    E --> F[[Submit for marking →]]
    F --> G[Result: mark gauge + score bars]
    G --> H[Feedback + test breakdown + verdicts]
    H --> I{Happy with the mark?}
    I -->|no| E
    I -->|yes| J[Done — see My submissions]

    style F fill:#6366f1,stroke:#a5b4fc,color:#fff
    style G fill:#065f46,stroke:#10b981,color:#fff
```

### What the result shows

| Element | Meaning |
|---------|---------|
| **Mark gauge** | Final mark /100 (green ≥70, amber ≥50, red below). |
| **Tests / Rubric / Style bars** | The three scores and their weights (default 60/25/15). |
| **Test breakdown** | Each test pass/fail; JUnit tests show **per-method** pills + **coverage**. |
| **Compile errors** | Your code with the **failing line highlighted**. |
| **Requirement verdicts** | Per rubric item: *met / partial / not met*, with line numbers. |
| **⚠ flagged for review** | Appears if the system suspects gaming (hard-coded answers). |

---

## 4. Lecturer (teacher) flow

Use the top-bar switch **Assignments & Marks ⟷ Analytics**.

### A. Create & publish an assignment

1. Click **+ New assignment**.
2. Fill in **Title**, choose **Language** (Python / Java), write the **Brief**.
3. *(Optional)* **Auto-draft rubric & tests** — generates starter rubric lines
   and a sample test to edit.
4. **Rubric** table — add requirements (**+ Add requirement**). Each has an
   **ID, text, Marks, Patterns**, and **Tests that prove it** (list the test IDs
   whose pass/fail decides the verdict — this is what catches wrong-for-the-task
   code). **Marks must total 100.**
5. **Test cases** table — add checks:
   - **+ I/O test** → give **stdin** and the **expected stdout** (±tolerance for
     numbers if set).
   - **+ JUnit test** (Java) → paste a **JUnit 5 test class**.
   - Tick **Approved** on each test you want used for marking.
6. Set the **Weights** (Tests / Rubric / Style) — must total 100.
7. **Save assignment**, then **Publish**.

> **Publish gate (FR-10):** at least one test, **every** test approved, rubric
> totalling 100, weights totalling 100 — otherwise publish is blocked with a
> message.

```mermaid
flowchart TD
    N[+ New assignment] --> T[Title, Language, Brief]
    T --> AD[/"Auto-draft rubric & tests (optional)"/]
    AD --> R[Edit rubric: marks total 100]
    T --> R
    R --> TC[Add tests: I/O and/or JUnit]
    TC --> AP[Approve each test]
    AP --> W[Set weights = 100]
    W --> SV[Save assignment]
    SV --> PUB{Publish checks pass?}
    PUB -->|no: fix rubric/tests| R
    PUB -->|yes| LIVE[📣 Published — students can submit]

    style LIVE fill:#065f46,stroke:#10b981,color:#fff
    style PUB fill:#92400e,stroke:#f59e0b,color:#fff
```

### B. Review & manage marks

In **Assignments & Marks**, pick the assignment → the **Class marks** table
lists every submission.

- **Override** — set a different final mark **with a reason**; the original mark
  is kept (audit) and the student sees the override.
- **Regrade** — re-run the grader for a submission.
- **Export CSV** — download all marks for the assignment.
- A **⚠ review** tag marks submissions the system flagged as possible gaming.

### C. Analytics

Switch to the **Analytics** tab:

- **Overview KPIs** — assignments, submissions, students, **average mark**,
  **pass rate**.
- **Per-assignment cards** — graded count, averages, **grade distribution**
  (A–F), and the **most-failed requirements** and **most-failed tests**, so you
  see exactly where the class struggles.

```mermaid
flowchart TD
    P[Published assignment] --> SUB[Students submit]
    SUB --> CM[Class marks table]
    CM --> OV[Override with reason]
    CM --> RG[Regrade]
    CM --> EX[Export CSV]
    SUB --> AN[Analytics: averages, pass rate,<br/>distribution, hardest items]

    style AN fill:#1e3a8a,stroke:#3b82f6,color:#fff
```

---

## 5. Lifecycles (state diagrams)

**Assignment** — only published assignments reach students:

```mermaid
stateDiagram-v2
    [*] --> draft: + New assignment
    draft --> draft: edit rubric / tests / weights
    draft --> published: Publish (all checks pass)
    published --> [*]
```

**Submission** — each student upload moves through these states:

```mermaid
stateDiagram-v2
    [*] --> pending: student submits
    pending --> running: grader picks it up
    running --> graded: result saved
    running --> failed: error (shown to student)
    graded --> graded: teacher override (original kept)
    failed --> pending: student resubmits
```

---

## 6. What happens when a student clicks Submit

```mermaid
sequenceDiagram
    participant St as 🎓 Student (browser)
    participant App as AutoMark server
    participant Gr as Grading engine
    St->>App: Submit code (POST /api/submit)
    App->>App: validate (signed in, ≤100KB, right type)
    App->>Gr: compile → run approved tests → style → rubric
    Gr-->>App: scores + feedback (mark computed in code)
    App-->>St: Result (gauge, breakdown, feedback)
    Note over St,App: Teacher later sees it in Class marks & Analytics
```

For *how* grading actually works (hidden tests, Checkstyle, JUnit, coverage,
anti-gaming, the Docker sandbox), see **README.md**. For first-time setup from a
clone, see **SETUP.md**.
