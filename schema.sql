PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('super_admin', 'dept_admin', 'teacher', 'student')),
    full_name TEXT NOT NULL,
    department_id INTEGER REFERENCES departments(id),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id)
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    semester INTEGER NOT NULL,
    section TEXT NOT NULL,
    academic_batch TEXT,
    registration_status TEXT NOT NULL DEFAULT 'APPROVED',
    user_id INTEGER REFERENCES users(id),
    cgpa REAL
);

CREATE TABLE IF NOT EXISTS teachers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    user_id INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS classrooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_code TEXT NOT NULL UNIQUE,
    capacity INTEGER NOT NULL,
    department_id INTEGER REFERENCES departments(id)
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    student_name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    semester INTEGER NOT NULL,
    section TEXT NOT NULL,
    academic_batch TEXT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    marks REAL NOT NULL,
    pass_marks REAL NOT NULL,
    result TEXT NOT NULL CHECK(result IN ('PASS', 'FAIL')),
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS remedial_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_code TEXT NOT NULL UNIQUE,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    semester INTEGER NOT NULL,
    section TEXT NOT NULL,
    academic_batch TEXT,
    teacher_id INTEGER REFERENCES teachers(id),
    classroom_id INTEGER REFERENCES classrooms(id),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'SCHEDULED', 'IN_PROGRESS', 'COMPLETED')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS remedial_students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES remedial_batches(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id),
    UNIQUE(batch_id, student_id)
);

CREATE TABLE IF NOT EXISTS timetable (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES remedial_batches(id) ON DELETE CASCADE,
    day_of_week TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    classroom_id INTEGER NOT NULL REFERENCES classrooms(id),
    teacher_id INTEGER NOT NULL REFERENCES teachers(id)
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timetable_id INTEGER REFERENCES timetable(id) ON DELETE CASCADE,
    batch_id INTEGER REFERENCES remedial_batches(id) ON DELETE CASCADE,
    student_id INTEGER NOT NULL REFERENCES students(id),
    attendance_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PRESENT', 'ABSENT')),
    marked_by INTEGER REFERENCES users(id),
    UNIQUE(batch_id, student_id, attendance_date)
);

CREATE TABLE IF NOT EXISTS teacher_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES remedial_batches(id) ON DELETE CASCADE,
    teacher_id INTEGER NOT NULL REFERENCES teachers(id),
    remarks TEXT,
    completion_status TEXT NOT NULL DEFAULT 'IN_PROGRESS' CHECK(completion_status IN ('IN_PROGRESS', 'COMPLETED')),
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS student_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    batch_id INTEGER NOT NULL REFERENCES remedial_batches(id),
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    comments TEXT,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(student_id, batch_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Regular semester attendance (entered/uploaded by admin), per subject
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
);

-- Extra class enrollment (students opt-in for after-college remedial 3:30-5:00 PM)
CREATE TABLE IF NOT EXISTS extra_class_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    batch_id INTEGER REFERENCES remedial_batches(id),
    status TEXT NOT NULL DEFAULT 'ENROLLED' CHECK(status IN ('ENROLLED', 'WITHDRAWN')),
    requested_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(student_id, subject_id, batch_id)
);

-- Peer mentor assignments (topper student assigned to help a remedial batch)
CREATE TABLE IF NOT EXISTS peer_mentors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mentor_student_id INTEGER NOT NULL REFERENCES students(id),
    batch_id INTEGER NOT NULL REFERENCES remedial_batches(id),
    assigned_by TEXT NOT NULL DEFAULT 'AUTO' CHECK(assigned_by IN ('AUTO', 'MANUAL')),
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(batch_id)
);

-- Subject-level backlog tracking with status (PENDING, CLEARED, NEEDS_VERIFICATION)
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
);

-- Configurable system settings (e.g. minimum attendance threshold)
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

