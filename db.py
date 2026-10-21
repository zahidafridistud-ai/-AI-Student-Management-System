"""Database layer: SQLite schema, authentication and role-based data access.

All official records (students, marks, attendance, users) live here. The AI
service never writes to this database.
"""

import hashlib
import hmac
import os
import random
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta

DB_PATH = os.environ.get("DB_PATH", "student_system.db")
ROLES = ("admin", "teacher", "student")
MIN_PASSWORD_LEN = 8
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 5
PBKDF2_ROUNDS = 200_000

DEFAULT_SETTINGS = {
    "institution_name": "Demo Institute",
    "attendance_threshold": "75",  # minimum attendance % required
    "pass_percentage": "40",
    "weak_threshold": "60",  # below this a subject "needs attention"
    "demo_data": "0",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT,
    program TEXT,
    semester INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','teacher','student')),
    full_name TEXT NOT NULL,
    student_id INTEGER REFERENCES students(id) ON DELETE SET NULL,
    active INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT
);
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    semester INTEGER NOT NULL DEFAULT 1,
    teacher_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS enrollments (
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    PRIMARY KEY (student_id, subject_id)
);
CREATE TABLE IF NOT EXISTS marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    semester INTEGER NOT NULL,
    assessment TEXT NOT NULL,
    obtained REAL NOT NULL,
    max_marks REAL NOT NULL,
    UNIQUE (student_id, subject_id, semester, assessment)
);
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    class_date TEXT NOT NULL,
    present INTEGER NOT NULL,
    UNIQUE (student_id, subject_id, class_date)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feedback_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved')),
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------------------
# Passwords & authentication
# --------------------------------------------------------------------------
def hash_password(password: str, salt: str = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS
    ).hex()
    return digest, salt


def authenticate(username: str, password: str):
    """Return (user_dict | None, error_message)."""
    username = (username or "").strip()
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            hash_password(password or "")  # keep timing similar for unknown users
            return None, "Invalid username or password."
        user = dict(row)
        if not user["active"]:
            return None, "This account has been deactivated."
        if user["locked_until"] and datetime.fromisoformat(user["locked_until"]) > datetime.now():
            return None, "Too many failed attempts. Try again in a few minutes."
        digest, _ = hash_password(password or "", user["salt"])
        if hmac.compare_digest(digest, user["password_hash"]):
            c.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?",
                (user["id"],),
            )
            return public_user(user), ""
        attempts = user["failed_attempts"] + 1
        locked = None
        if attempts >= MAX_FAILED_LOGINS:
            locked = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).isoformat()
            attempts = 0
        c.execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
            (attempts, locked, user["id"]),
        )
        return None, "Invalid username or password."


def public_user(row: dict) -> dict:
    return {k: row[k] for k in ("id", "username", "role", "full_name", "student_id")}


def get_user(user_id: int):
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone()
    return public_user(dict(row)) if row else None


def create_user(username, password, role, full_name, student_id=None):
    if role not in ROLES:
        raise ValueError("Invalid role.")
    username = username.strip()
    if not username or not full_name.strip():
        raise ValueError("Username and full name are required.")
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
    if role == "student" and not student_id:
        raise ValueError("A student account must be linked to a student record.")
    digest, salt = hash_password(password)
    try:
        with conn() as c:
            cur = c.execute(
                "INSERT INTO users (username, password_hash, salt, role, full_name, student_id) "
                "VALUES (?,?,?,?,?,?)",
                (username, digest, salt, role, full_name.strip(),
                 student_id if role == "student" else None),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError("That username already exists.") from exc


def change_password(user_id: int, old_password: str, new_password: str):
    if len(new_password) < MIN_PASSWORD_LEN:
        raise ValueError(f"New password must be at least {MIN_PASSWORD_LEN} characters.")
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("User not found.")
        digest, _ = hash_password(old_password, row["salt"])
        if not hmac.compare_digest(digest, row["password_hash"]):
            raise ValueError("Current password is incorrect.")
        new_digest, new_salt = hash_password(new_password)
        c.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
            (new_digest, new_salt, user_id),
        )


def list_users():
    with conn() as c:
        return _rows(c.execute(
            "SELECT id, username, full_name, role, student_id, active FROM users ORDER BY role, username"
        ))


def set_user_active(admin: dict, user_id: int, active: bool):
    _require_admin(admin)
    if admin["id"] == user_id and not active:
        raise ValueError("You cannot deactivate your own account.")
    with conn() as c:
        c.execute("UPDATE users SET active = ? WHERE id = ?", (1 if active else 0, user_id))


def _require_admin(user):
    if not user or user["role"] != "admin":
        raise PermissionError("Administrator access required.")


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def get_settings() -> dict:
    with conn() as c:
        stored = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
    raw = {**DEFAULT_SETTINGS, **stored}

    def num(key):
        try:
            return float(raw[key])
        except ValueError:
            return float(DEFAULT_SETTINGS[key])

    return {
        "institution_name": raw["institution_name"],
        "attendance_threshold": num("attendance_threshold"),
        "pass_percentage": num("pass_percentage"),
        "weak_threshold": num("weak_threshold"),
        "demo_data": raw["demo_data"] == "1",
    }


def save_settings(admin: dict, values: dict):
    _require_admin(admin)
    for key in ("attendance_threshold", "pass_percentage", "weak_threshold"):
        if key in values and not (0 <= float(values[key]) <= 100):
            raise ValueError("Thresholds must be between 0 and 100.")
    with conn() as c:
        for key, value in values.items():
            if key in DEFAULT_SETTINGS:
                c.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )


# --------------------------------------------------------------------------
# Role-based access to student data
# --------------------------------------------------------------------------
def scope_subject_ids(user: dict) -> list:
    """Subjects this user is allowed to see data for."""
    with conn() as c:
        if user["role"] == "admin":
            cur = c.execute("SELECT id FROM subjects")
        elif user["role"] == "teacher":
            cur = c.execute("SELECT id FROM subjects WHERE teacher_id = ?", (user["id"],))
        else:
            cur = c.execute(
                "SELECT subject_id AS id FROM enrollments WHERE student_id = ?",
                (user["student_id"] or -1,),
            )
        return [r["id"] for r in cur.fetchall()]


def visible_students(user: dict) -> list:
    with conn() as c:
        if user["role"] == "admin":
            cur = c.execute("SELECT * FROM students ORDER BY name")
        elif user["role"] == "teacher":
            cur = c.execute(
                "SELECT DISTINCT st.* FROM students st "
                "JOIN enrollments e ON e.student_id = st.id "
                "JOIN subjects su ON su.id = e.subject_id "
                "WHERE su.teacher_id = ? ORDER BY st.name",
                (user["id"],),
            )
        else:
            cur = c.execute("SELECT * FROM students WHERE id = ?", (user["student_id"] or -1,))
        return _rows(cur)


def can_view_student(user: dict, student_id: int) -> bool:
    return any(s["id"] == student_id for s in visible_students(user))


def student_records(user: dict, student_id: int) -> dict:
    """Records of one student, limited to what `user` may see."""
    if not can_view_student(user, student_id):
        raise PermissionError("You are not allowed to view this student's records.")
    allowed = set(scope_subject_ids(user))
    with conn() as c:
        student = dict(c.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone())
        subjects = [
            s for s in _rows(c.execute(
                "SELECT su.id, su.code, su.name, su.semester FROM subjects su "
                "JOIN enrollments e ON e.subject_id = su.id WHERE e.student_id = ? "
                "ORDER BY su.semester, su.code", (student_id,)))
            if s["id"] in allowed
        ]
        ids = [s["id"] for s in subjects]
        marks = [m for m in _rows(c.execute(
            "SELECT subject_id, semester, assessment, obtained, max_marks FROM marks "
            "WHERE student_id = ?", (student_id,))) if m["subject_id"] in ids]
        att = [a for a in _rows(c.execute(
            "SELECT subject_id, class_date, present FROM attendance WHERE student_id = ?",
            (student_id,))) if a["subject_id"] in ids]
    return {"student": student, "subjects": subjects, "marks": marks, "attendance": att}


def editable_subjects(user: dict) -> list:
    if user["role"] not in ("admin", "teacher"):
        return []
    with conn() as c:
        if user["role"] == "admin":
            return _rows(c.execute("SELECT * FROM subjects ORDER BY semester, code"))
        return _rows(c.execute(
            "SELECT * FROM subjects WHERE teacher_id = ? ORDER BY semester, code", (user["id"],)))


def _require_subject_access(user, subject_id):
    if subject_id not in [s["id"] for s in editable_subjects(user)]:
        raise PermissionError("You are not allowed to edit this subject.")


def enrolled_students(user: dict, subject_id: int) -> list:
    _require_subject_access(user, subject_id)
    with conn() as c:
        return _rows(c.execute(
            "SELECT st.id, st.roll_no, st.name FROM students st "
            "JOIN enrollments e ON e.student_id = st.id WHERE e.subject_id = ? ORDER BY st.name",
            (subject_id,)))


def get_marks_entry(user, subject_id, semester, assessment) -> dict:
    _require_subject_access(user, subject_id)
    with conn() as c:
        cur = c.execute(
            "SELECT student_id, obtained, max_marks FROM marks "
            "WHERE subject_id = ? AND semester = ? AND assessment = ?",
            (subject_id, semester, assessment))
        return {r["student_id"]: r["obtained"] for r in cur.fetchall()}


def save_marks(user, subject_id, semester, assessment, max_marks, values: dict) -> int:
    """values: {student_id: obtained or None}. Returns number of rows saved."""
    _require_subject_access(user, subject_id)
    assessment = assessment.strip()
    if not assessment:
        raise ValueError("Assessment name is required.")
    if max_marks <= 0:
        raise ValueError("Maximum marks must be greater than 0.")
    valid = {s["id"] for s in enrolled_students(user, subject_id)}
    saved = 0
    with conn() as c:
        for sid, obtained in values.items():
            if obtained is None or obtained != obtained:  # None or NaN
                continue
            if sid not in valid:
                raise PermissionError("Student is not enrolled in this subject.")
            if not (0 <= float(obtained) <= max_marks):
                raise ValueError(f"Marks must be between 0 and {max_marks:g}.")
            c.execute(
                "INSERT INTO marks (student_id, subject_id, semester, assessment, obtained, max_marks) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(student_id, subject_id, semester, assessment) "
                "DO UPDATE SET obtained = excluded.obtained, max_marks = excluded.max_marks",
                (sid, subject_id, semester, assessment, float(obtained), float(max_marks)))
            saved += 1
    return saved


def get_attendance_entry(user, subject_id, class_date) -> dict:
    _require_subject_access(user, subject_id)
    with conn() as c:
        cur = c.execute(
            "SELECT student_id, present FROM attendance WHERE subject_id = ? AND class_date = ?",
            (subject_id, str(class_date)))
        return {r["student_id"]: bool(r["present"]) for r in cur.fetchall()}


def save_attendance(user, subject_id, class_date, values: dict) -> int:
    _require_subject_access(user, subject_id)
    valid = {s["id"] for s in enrolled_students(user, subject_id)}
    with conn() as c:
        for sid, present in values.items():
            if sid not in valid:
                raise PermissionError("Student is not enrolled in this subject.")
            c.execute(
                "INSERT INTO attendance (student_id, subject_id, class_date, present) VALUES (?,?,?,?) "
                "ON CONFLICT(student_id, subject_id, class_date) DO UPDATE SET present = excluded.present",
                (sid, subject_id, str(class_date), 1 if present else 0))
    return len(values)


# --------------------------------------------------------------------------
# Teacher feedback drafts (AI drafts must be reviewed before they are final)
# --------------------------------------------------------------------------
def save_feedback(user, student_id, text, approve=False, draft_id=None) -> int:
    if user["role"] not in ("admin", "teacher"):
        raise PermissionError("Only staff can write feedback.")
    if not can_view_student(user, student_id):
        raise PermissionError("You are not allowed to give feedback to this student.")
    text = text.strip()
    if not text:
        raise ValueError("Feedback text is empty.")
    status = "approved" if approve else "draft"
    now = datetime.now().isoformat(timespec="seconds")
    with conn() as c:
        if draft_id:
            row = c.execute("SELECT author_id FROM feedback_drafts WHERE id = ?", (draft_id,)).fetchone()
            if row is None or (user["role"] != "admin" and row["author_id"] != user["id"]):
                raise PermissionError("You cannot edit this draft.")
            c.execute(
                "UPDATE feedback_drafts SET text = ?, status = ?, reviewed_at = ? WHERE id = ?",
                (text, status, now if approve else None, draft_id))
            return draft_id
        cur = c.execute(
            "INSERT INTO feedback_drafts (student_id, author_id, text, status, created_at, reviewed_at) "
            "VALUES (?,?,?,?,?,?)",
            (student_id, user["id"], text, status, now, now if approve else None))
        return cur.lastrowid


def list_feedback(user) -> list:
    with conn() as c:
        base = ("SELECT f.id, f.student_id, st.name AS student_name, f.text, f.status, f.created_at "
                "FROM feedback_drafts f JOIN students st ON st.id = f.student_id ")
        if user["role"] == "admin":
            return _rows(c.execute(base + "ORDER BY f.created_at DESC"))
        return _rows(c.execute(base + "WHERE f.author_id = ? ORDER BY f.created_at DESC", (user["id"],)))


def approved_feedback(user) -> list:
    """Teacher-approved feedback visible to the logged-in student only."""
    if user["role"] != "student" or not user["student_id"]:
        return []
    with conn() as c:
        return _rows(c.execute(
            "SELECT text, reviewed_at FROM feedback_drafts WHERE student_id = ? AND status = 'approved' "
            "ORDER BY reviewed_at DESC", (user["student_id"],)))


# --------------------------------------------------------------------------
# Administration
# --------------------------------------------------------------------------
def add_student(admin, roll_no, name, email, program, semester) -> int:
    _require_admin(admin)
    if not roll_no.strip() or not name.strip():
        raise ValueError("Roll number and name are required.")
    try:
        with conn() as c:
            return c.execute(
                "INSERT INTO students (roll_no, name, email, program, semester) VALUES (?,?,?,?,?)",
                (roll_no.strip(), name.strip(), email.strip(), program.strip(), int(semester))).lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError("That roll number already exists.") from exc


def add_subject(admin, code, name, semester, teacher_id=None) -> int:
    _require_admin(admin)
    if not code.strip() or not name.strip():
        raise ValueError("Subject code and name are required.")
    try:
        with conn() as c:
            return c.execute(
                "INSERT INTO subjects (code, name, semester, teacher_id) VALUES (?,?,?,?)",
                (code.strip(), name.strip(), int(semester), teacher_id)).lastrowid
    except sqlite3.IntegrityError as exc:
        raise ValueError("That subject code already exists.") from exc


def set_enrollments(admin, student_id, subject_ids):
    _require_admin(admin)
    with conn() as c:
        c.execute("DELETE FROM enrollments WHERE student_id = ?", (student_id,))
        c.executemany("INSERT INTO enrollments (student_id, subject_id) VALUES (?,?)",
                      [(student_id, sid) for sid in subject_ids])


def all_students():
    with conn() as c:
        return _rows(c.execute("SELECT * FROM students ORDER BY name"))


def all_subjects():
    with conn() as c:
        return _rows(c.execute(
            "SELECT su.*, u.full_name AS teacher_name FROM subjects su "
            "LEFT JOIN users u ON u.id = su.teacher_id ORDER BY su.semester, su.code"))


def enrollment_ids(student_id):
    with conn() as c:
        return [r["subject_id"] for r in c.execute(
            "SELECT subject_id FROM enrollments WHERE student_id = ?", (student_id,))]


def teachers():
    with conn() as c:
        return _rows(c.execute("SELECT id, full_name FROM users WHERE role = 'teacher' AND active = 1"))


def remove_demo_data(admin):
    """Delete all demo records but keep the logged-in administrator."""
    _require_admin(admin)
    with conn() as c:
        for table in ("feedback_drafts", "attendance", "marks", "enrollments", "subjects", "students"):
            c.execute(f"DELETE FROM {table}")
        c.execute("DELETE FROM users WHERE id != ?", (admin["id"],))
        c.execute("INSERT INTO settings (key, value) VALUES ('demo_data', '0') "
                  "ON CONFLICT(key) DO UPDATE SET value = '0'")


# --------------------------------------------------------------------------
# Initialisation and DEMO data
# --------------------------------------------------------------------------
def init_db():
    with conn() as c:
        c.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        empty = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    if empty:
        seed_demo_data()


DEMO_ADMIN = ("admin", "Admin@123")
DEMO_TEACHERS = [("teacher1", "Teacher@123", "Mr. Hassan Raza"), ("teacher2", "Teacher@123", "Ms. Sara Ahmed")]
DEMO_STUDENTS = [
    # name, base %, attendance probability  (subjects: MATH, PROG, DB, DS, ENG)
    ("Ayesha Khan", [45, 82, 58, 70, 66], 0.68),
    ("Bilal Ahmed", [78, 85, 80, 76, 72], 0.94),
    ("Chloe Martin", [62, 55, 60, 58, 70], 0.88),
    ("Daniyal Shah", [35, 48, 40, 44, 52], 0.60),
    ("Emaan Fatima", [90, 88, 92, 85, 80], 0.97),
    ("Farhan Ali", [68, 72, 64, 70, 66], 0.82),
    ("Hira Noor", [55, 61, 49, 52, 74], 0.76),
    ("Imran Malik", [72, 38, 66, 60, 58], 0.85),
]


def seed_demo_data():
    """Fill an empty database with clearly marked DEMO data (fake people, fake marks)."""
    rnd = random.Random(2024)
    adm_h, adm_s = hash_password(DEMO_ADMIN[1])
    with conn() as c:
        c.execute("INSERT INTO users (username, password_hash, salt, role, full_name) VALUES (?,?,?,?,?)",
                  (DEMO_ADMIN[0], adm_h, adm_s, "admin", "Demo Administrator"))
        teacher_ids = []
        for uname, pw, full in DEMO_TEACHERS:
            h, s = hash_password(pw)
            teacher_ids.append(c.execute(
                "INSERT INTO users (username, password_hash, salt, role, full_name) VALUES (?,?,?,?,?)",
                (uname, h, s, "teacher", full)).lastrowid)
        t1, t2 = teacher_ids
        cur_subjects = [("CS201", "Mathematics", 3, t1), ("CS202", "Programming", 3, t2),
                        ("CS203", "Database Systems", 3, t2), ("CS204", "Data Structures", 3, t2),
                        ("CS205", "English Communication", 3, t1)]
        old_subjects = [("CS101", "Calculus", 2, t1), ("CS102", "Intro to Programming", 2, t2),
                        ("CS103", "Digital Logic", 2, t2)]
        sub_ids = [c.execute("INSERT INTO subjects (code, name, semester, teacher_id) VALUES (?,?,?,?)", s).lastrowid
                   for s in cur_subjects]
        old_ids = [c.execute("INSERT INTO subjects (code, name, semester, teacher_id) VALUES (?,?,?,?)", s).lastrowid
                   for s in old_subjects]

        days, d = [], date.today()
        while len(days) < 20:
            d -= timedelta(days=1)
            if d.weekday() < 5:
                days.append(d.isoformat())
        assessments = [("Quiz", 20), ("Assignment", 10), ("Midterm", 30)]

        for i, (name, base, att_p) in enumerate(DEMO_STUDENTS, 1):
            sid = c.execute(
                "INSERT INTO students (roll_no, name, email, program, semester) VALUES (?,?,?,?,?)",
                (f"DEMO-{i:03d}", name, f"demo{i}@example.com", "BS Computer Science", 3)).lastrowid
            uname = f"student{i}"
            h, s = hash_password("Student@123")
            c.execute("INSERT INTO users (username, password_hash, salt, role, full_name, student_id) "
                      "VALUES (?,?,?,?,?,?)", (uname, h, s, "student", name, sid))
            for sub_id, pct in zip(sub_ids, base):
                c.execute("INSERT INTO enrollments VALUES (?,?)", (sid, sub_id))
                for a_name, a_max in assessments:
                    noise = 0 if i == 1 else rnd.uniform(-1.5, 1.5)  # first demo student matches the spec example
                    got = max(0, min(a_max, round(a_max * pct / 100 + noise)))
                    c.execute("INSERT INTO marks (student_id, subject_id, semester, assessment, obtained, max_marks) "
                              "VALUES (?,?,?,?,?,?)", (sid, sub_id, 3, a_name, got, a_max))
                for day in days:
                    c.execute("INSERT INTO attendance (student_id, subject_id, class_date, present) VALUES (?,?,?,?)",
                              (sid, sub_id, day, 1 if rnd.random() < att_p else 0))
            for sub_id in old_ids:  # previous semester results (marks only)
                c.execute("INSERT INTO enrollments VALUES (?,?)", (sid, sub_id))
                pct = max(20, min(98, sum(base) / len(base) + rnd.uniform(-8, 8)))
                for a_name, a_max in assessments:
                    c.execute("INSERT INTO marks (student_id, subject_id, semester, assessment, obtained, max_marks) "
                              "VALUES (?,?,?,?,?,?)", (sid, sub_id, 2, a_name, round(a_max * pct / 100), a_max))
        c.execute("INSERT INTO settings (key, value) VALUES ('demo_data', '1') "
                  "ON CONFLICT(key) DO UPDATE SET value = '1'")
