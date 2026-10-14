"""
Clear the existing database records and reset the system for fresh testing.
- Backs up the database before clearing.
- Removes existing student, attendance, result, backlog, remedial allocation, and other test records.
- Preserves administrator and mentor accounts, classrooms, departments, and core subjects.
- Preserves database structure, tables, and settings.
"""

import shutil
from datetime import datetime
from pathlib import Path

from db import DB_PATH, get_db, init_db, set_setting

CLEAR_TABLES = [
    "attendance",
    "batch_attendance",
    "student_feedback",
    "teacher_reports",
    "timetable",
    "remedial_students",
    "peer_mentors",
    "extra_class_requests",
    "student_backlog_subjects",
    "semester_attendance",
    "remedial_batches",
    "results",
    "notifications",
    "audit_logs",
    "students",
]


def backup_database():
    """Create timestamped backup of the database before clearing."""
    if not DB_PATH.exists():
        print("Database file does not exist yet. No backup needed.")
        return None
    backup_filename = f"remedial_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    backup_path = DB_PATH.parent / backup_filename
    shutil.copy2(DB_PATH, backup_path)
    print(f"Database backed up to: {backup_path}")
    return backup_path


def clear_operational_data(conn):
    """Clear all test/operational records while preserving accounts and schema."""
    conn.execute("PRAGMA foreign_keys = OFF")

    for table in CLEAR_TABLES:
        conn.execute(f"DELETE FROM {table}")

    # Remove student user accounts; preserve superadmin, dept_admins, and teachers
    conn.execute("DELETE FROM users WHERE role = 'student'")

    # Reset auto-increment counters for cleared tables
    table_names_sql = ",".join(f"'{t}'" for t in CLEAR_TABLES)
    conn.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({table_names_sql})")

    # Set default configuration
    set_setting("min_attendance_threshold", "75", conn=conn)

    conn.execute("PRAGMA foreign_keys = ON")


def reset_database():
    """Perform database backup and complete reset."""
    backup_path = backup_database()
    init_db()
    with get_db() as conn:
        clear_operational_data(conn)
        conn.commit()

    print("\nDatabase reset complete:")
    print("  - Cleared: students, results, backlogs, attendance, batches, timetables, notifications, student accounts.")
    print("  - Preserved: administrator accounts, mentor/teacher accounts, departments, classrooms, subjects, system settings.")
    print("  - Default minimum attendance threshold set to 75%.")
    print("  - Ready for fresh testing and record imports.")


if __name__ == "__main__":
    reset_database()
