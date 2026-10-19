import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DB_PATH = INSTANCE_DIR / "remedial.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def get_db():
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
        conn.commit()


def _add_column_if_missing(conn, table, column, definition):
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_semester_attendance_subjects(conn):
    """Store attendance per subject and unique on (student, semester, batch, subject)."""
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='semester_attendance'"
    ).fetchone()
    if not exists:
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(semester_attendance)").fetchall()}
    unique_cols = []
    for idx in conn.execute("PRAGMA index_list(semester_attendance)").fetchall():
        if not idx["unique"]:
            continue
        names = [
            r["name"]
            for r in conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()
        ]
        if "student_id" in names and "semester" in names:
            unique_cols = names
            break
    needs_rebuild = "subject_id" not in cols or "subject_id" not in unique_cols
    if not needs_rebuild:
        return
    conn.execute(
        """
        CREATE TABLE semester_attendance_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES students(id),
            semester INTEGER NOT NULL,
            academic_batch TEXT,
            subject_id INTEGER REFERENCES subjects(id),
            attendance_percent REAL NOT NULL DEFAULT 0,
            total_classes INTEGER DEFAULT 0,
            classes_attended INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(student_id, semester, academic_batch, subject_id)
        )
        """
    )
    has_subject = "subject_id" in cols
    subject_select = "subject_id" if has_subject else "NULL"
    conn.execute(
        f"""
        INSERT OR IGNORE INTO semester_attendance_new
            (student_id, semester, academic_batch, subject_id,
             attendance_percent, total_classes, classes_attended, updated_at)
        SELECT student_id, semester, academic_batch, {subject_select},
               attendance_percent, total_classes, classes_attended, updated_at
        FROM semester_attendance
        """
    )
    conn.execute("DROP TABLE semester_attendance")
    conn.execute("ALTER TABLE semester_attendance_new RENAME TO semester_attendance")


def _migrate_attendance_table(conn):
    """Ensure timetable_id in attendance table is nullable and constraints are intact."""
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='attendance'"
    ).fetchone()
    if not exists:
        return
    cols = {row[1]: dict(row) for row in conn.execute("PRAGMA table_info(attendance)").fetchall()}
    if cols.get("timetable_id", {}).get("notnull") == 1:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timetable_id INTEGER REFERENCES timetable(id) ON DELETE CASCADE,
                batch_id INTEGER REFERENCES remedial_batches(id) ON DELETE CASCADE,
                student_id INTEGER NOT NULL REFERENCES students(id),
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('PRESENT', 'ABSENT')),
                marked_by INTEGER REFERENCES users(id),
                UNIQUE(batch_id, student_id, attendance_date)
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO attendance_new
                (id, timetable_id, batch_id, student_id, attendance_date, status, marked_by)
            SELECT id, timetable_id, batch_id, student_id, attendance_date, status, marked_by
            FROM attendance
            """
        )
        conn.execute("DROP TABLE attendance")
        conn.execute("ALTER TABLE attendance_new RENAME TO attendance")
        conn.execute("PRAGMA foreign_keys = ON")

    # Sync any records from batch_attendance to attendance
    has_batch_att = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='batch_attendance'"
    ).fetchone()
    if has_batch_att:
        conn.execute(
            """
            INSERT OR IGNORE INTO attendance (batch_id, student_id, attendance_date, status, marked_by)
            SELECT batch_id, student_id, attendance_date, status, marked_by FROM batch_attendance
            """
        )


def _migrate(conn):
    """Apply lightweight migrations for existing databases."""
    _migrate_attendance_table(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(attendance)").fetchall()}
    if "batch_id" not in cols:
        conn.execute("ALTER TABLE attendance ADD COLUMN batch_id INTEGER REFERENCES remedial_batches(id)")
    _add_column_if_missing(conn, "students", "academic_batch", "TEXT")
    _add_column_if_missing(conn, "students", "registration_status", "TEXT DEFAULT 'APPROVED'")
    _add_column_if_missing(conn, "students", "cgpa", "REAL")
    _add_column_if_missing(conn, "students", "email", "TEXT")
    _add_column_if_missing(conn, "teachers", "email", "TEXT")
    _add_column_if_missing(conn, "users", "email", "TEXT")
    _add_column_if_missing(conn, "results", "academic_batch", "TEXT")
    _add_column_if_missing(conn, "remedial_batches", "academic_batch", "TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS batch_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL REFERENCES remedial_batches(id) ON DELETE CASCADE,
            student_id INTEGER NOT NULL REFERENCES students(id),
            attendance_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PRESENT', 'ABSENT')),
            marked_by INTEGER REFERENCES users(id),
            UNIQUE(batch_id, student_id, attendance_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS extra_class_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL REFERENCES remedial_batches(id) ON DELETE CASCADE,
            student_id INTEGER NOT NULL REFERENCES students(id),
            attendance_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('PRESENT', 'ABSENT')),
            marked_by INTEGER REFERENCES users(id),
            remarks TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(batch_id, student_id, attendance_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            token TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "UPDATE students SET registration_status = 'APPROVED' WHERE registration_status IS NULL OR registration_status = ''"
    )
    # --- System settings table ---
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    # Set default min_attendance_threshold if missing
    existing_threshold = conn.execute(
        "SELECT value FROM system_settings WHERE key = 'min_attendance_threshold'"
    ).fetchone()
    if not existing_threshold:
        conn.execute(
            "INSERT INTO system_settings (key, value) VALUES ('min_attendance_threshold', '75')"
        )

    # --- New tables for weak student, extra classes, peer mentors ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS semester_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES students(id),
            semester INTEGER NOT NULL,
            academic_batch TEXT,
            subject_id INTEGER REFERENCES subjects(id),
            attendance_percent REAL NOT NULL DEFAULT 0,
            total_classes INTEGER DEFAULT 0,
            classes_attended INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(student_id, semester, academic_batch, subject_id)
        )
    """)
    _migrate_semester_attendance_subjects(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS extra_class_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES students(id),
            subject_id INTEGER NOT NULL REFERENCES subjects(id),
            batch_id INTEGER REFERENCES remedial_batches(id),
            status TEXT NOT NULL DEFAULT 'ENROLLED' CHECK(status IN ('ENROLLED', 'WITHDRAWN')),
            requested_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(student_id, subject_id, batch_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS peer_mentors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mentor_student_id INTEGER NOT NULL REFERENCES students(id),
            batch_id INTEGER NOT NULL REFERENCES remedial_batches(id),
            assigned_by TEXT NOT NULL DEFAULT 'AUTO' CHECK(assigned_by IN ('AUTO', 'MANUAL')),
            assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(batch_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS student_backlog_subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES students(id),
            subject_id INTEGER NOT NULL REFERENCES subjects(id),
            original_semester INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'CLEARED', 'NEEDS_VERIFICATION')),
            year_back_status TEXT CHECK(year_back_status IN ('YES', 'NO', 'VERIFY')),
            verified_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(student_id, subject_id, original_semester)
        )
    """)
    conn.execute(
        "UPDATE timetable SET start_time = '15:30', end_time = '16:30' WHERE start_time != '15:30' OR end_time != '16:30'"
    )
    _ensure_computer_resources(conn)


def _ensure_computer_resources(conn):
    co = conn.execute("SELECT id FROM departments WHERE code = 'CO'").fetchone()
    if not co:
        return
    co_id = co["id"]
    extra_rooms = [
        ("LAB-CO-2", 40),
        ("LAB-CO-3", 40),
        ("ROOM-CO-101", 50),
        ("ROOM-CO-102", 50),
    ]
    for room_code, capacity in extra_rooms:
        conn.execute(
            "INSERT OR IGNORE INTO classrooms (room_code, capacity, department_id) VALUES (?, ?, ?)",
            (room_code, capacity, co_id),
        )


def _ensure_demo_student(conn):
    """Create demo student A24DCE01 / 1234 without touching other accounts."""
    co = conn.execute("SELECT id FROM departments WHERE code = 'CO'").fetchone()
    if not co:
        return
    co_id = co["id"]
    roll = "A24DCE01"
    full_name = "Demo Student"

    existing_user = conn.execute(
        "SELECT id FROM users WHERE username = ?", (roll,)
    ).fetchone()
    if not existing_user:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, role, full_name, department_id)
            VALUES (?, ?, 'student', ?, ?)
            """,
            (roll, generate_password_hash("1234"), full_name, co_id),
        )
    user_id = conn.execute(
        "SELECT id FROM users WHERE username = ?", (roll,)
    ).fetchone()["id"]

    student = conn.execute(
        "SELECT id, full_name FROM students WHERE student_id = ?", (roll,)
    ).fetchone()
    if not student:
        conn.execute(
            """
            INSERT INTO students
            (student_id, full_name, department_id, semester, section, academic_batch, user_id)
            VALUES (?, ?, ?, 2, 'A', '2024-2027', ?)
            """,
            (roll, full_name, co_id, user_id),
        )
    else:
        conn.execute(
            "UPDATE students SET user_id = COALESCE(user_id, ?) WHERE student_id = ?",
            (user_id, roll),
        )
        conn.execute(
            "UPDATE users SET full_name = ? WHERE id = ? AND role = 'student'",
            (student["full_name"], user_id),
        )
    student_pk = conn.execute(
        "SELECT id FROM students WHERE student_id = ?", (roll,)
    ).fetchone()["id"]

    demo_subjects = [
        ("MATH201", "Engineering Mathematics"),
        ("PROG101", "Programming Fundamentals"),
        ("DS201", "Data Structures"),
    ]
    subject_ids = {}
    for code, name in demo_subjects:
        row = conn.execute(
            "SELECT id FROM subjects WHERE code = ? OR name = ?", (code, name)
        ).fetchone()
        if not row:
            cur = conn.execute(
                "INSERT INTO subjects (code, name, department_id) VALUES (?, ?, ?)",
                (code, name, co_id),
            )
            subject_ids[code] = cur.lastrowid
        else:
            subject_ids[code] = row["id"]

    for code, marks in (("MATH201", 28), ("PROG101", 32), ("DS201", 22)):
        subject_id = subject_ids[code]
        existing = conn.execute(
            "SELECT id FROM results WHERE student_id = ? AND subject_id = ?",
            (roll, subject_id),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT INTO results
            (student_id, student_name, department_id, semester, section,
             academic_batch, subject_id, marks, pass_marks, result)
            VALUES (?, ?, ?, 2, 'A', '2024-2027', ?, ?, 40, 'FAIL')
            """,
            (roll, full_name, co_id, subject_id, marks),
        )

    teacher = conn.execute(
        "SELECT id FROM teachers WHERE department_id = ? LIMIT 1", (co_id,)
    ).fetchone()
    classroom = conn.execute(
        "SELECT id FROM classrooms WHERE department_id = ? LIMIT 1", (co_id,)
    ).fetchone()
    if not teacher or not classroom:
        return

    demo_batches = [
        ("DEMO-A24-PROG", "PROG101", "Monday", "15:30", "16:30"),
        ("DEMO-A24-MATH", "MATH201", "Wednesday", "15:30", "16:30"),
        ("DEMO-A24-DS", "DS201", "Friday", "15:30", "16:30"),
    ]
    batch_ids = {}
    for batch_code, subject_code, day, start, end in demo_batches:
        batch = conn.execute(
            "SELECT id FROM remedial_batches WHERE batch_code = ?", (batch_code,)
        ).fetchone()
        if not batch:
            cur = conn.execute(
                """
                INSERT INTO remedial_batches
                (batch_code, department_id, subject_id, semester, section, academic_batch,
                 teacher_id, classroom_id, status)
                VALUES (?, ?, ?, 2, 'A', '2024-2027', ?, ?, 'SCHEDULED')
                """,
                (batch_code, co_id, subject_ids[subject_code], teacher["id"], classroom["id"]),
            )
            batch_id = cur.lastrowid
        else:
            batch_id = batch["id"]
        batch_ids[subject_code] = batch_id
        conn.execute(
            "INSERT OR IGNORE INTO remedial_students (batch_id, student_id) VALUES (?, ?)",
            (batch_id, student_pk),
        )
        slot_exists = conn.execute(
            "SELECT id FROM timetable WHERE batch_id = ? LIMIT 1", (batch_id,)
        ).fetchone()
        if not slot_exists:
            conn.execute(
                """
                INSERT INTO timetable (batch_id, day_of_week, start_time, end_time, classroom_id, teacher_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (batch_id, day, start, end, classroom["id"], teacher["id"]),
            )

    att_exists = conn.execute(
        "SELECT 1 FROM batch_attendance WHERE student_id = ? LIMIT 1", (student_pk,)
    ).fetchone()
    if att_exists:
        return

    demo_days = {
        "PROG101": [
            ("2026-09-01", "PRESENT"),
            ("2026-09-08", "ABSENT"),
            ("2026-09-15", "PRESENT"),
            ("2026-09-21", "PRESENT"),
        ],
        "MATH201": [
            ("2026-09-03", "PRESENT"),
            ("2026-09-10", "PRESENT"),
            ("2026-09-17", "ABSENT"),
        ],
        "DS201": [
            ("2026-09-05", "PRESENT"),
            ("2026-09-12", "ABSENT"),
            ("2026-09-19", "PRESENT"),
        ],
    }
    for subject_code, days in demo_days.items():
        batch_id = batch_ids[subject_code]
        for day, status in days:
            conn.execute(
                """
                INSERT OR IGNORE INTO batch_attendance
                (batch_id, student_id, attendance_date, status)
                VALUES (?, ?, ?, ?)
                """,
                (batch_id, student_pk, day, status),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO attendance
                (batch_id, student_id, attendance_date, status)
                VALUES (?, ?, ?, ?)
                """,
                (batch_id, student_pk, day, status),
            )


def log_audit(user_id, action, details=None, conn=None):
    if conn is not None:
        conn.execute(
            "INSERT INTO audit_logs (user_id, action, details) VALUES (?, ?, ?)",
            (user_id, action, details),
        )
        return
    with get_db() as local_conn:
        local_conn.execute(
            "INSERT INTO audit_logs (user_id, action, details) VALUES (?, ?, ?)",
            (user_id, action, details),
        )
        local_conn.commit()


def add_notification(user_id, message, conn=None):
    if conn is not None:
        conn.execute(
            "INSERT INTO notifications (user_id, message) VALUES (?, ?)",
            (user_id, message),
        )
        return
    with get_db() as local_conn:
        local_conn.execute(
            "INSERT INTO notifications (user_id, message) VALUES (?, ?)",
            (user_id, message),
        )
        local_conn.commit()


def get_setting(key, default=None, conn=None):
    """Retrieve system configuration value."""
    def _execute(c):
        row = c.execute("SELECT value FROM system_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    if conn is not None:
        return _execute(conn)
    with get_db() as local_conn:
        return _execute(local_conn)


def set_setting(key, value, conn=None):
    """Save or update system configuration value."""
    def _execute(c):
        c.execute(
            """
            INSERT INTO system_settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')
            """,
            (key, str(value)),
        )

    if conn is not None:
        _execute(conn)
    else:
        with get_db() as local_conn:
            _execute(local_conn)
            local_conn.commit()

