import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "remedial.db"

def test_clear_database():
    """Test the database clearing logic"""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    
    # Get counts before deletion for logging
    student_count = conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"]
    teacher_count = conn.execute("SELECT COUNT(*) AS c FROM teachers").fetchone()["c"]
    batch_count = conn.execute("SELECT COUNT(*) AS c FROM remedial_batches").fetchone()["c"]
    
    print(f"Before clear: {student_count} students, {teacher_count} teachers, {batch_count} batches")
    
    # Preserve Super Admin account
    super_admin_id = conn.execute("SELECT id FROM users WHERE role = 'super_admin' LIMIT 1").fetchone()
    
    # Delete in order of dependencies (departments are NOT deleted - they are preserved)
    tables_to_clear = [
        "timetable",
        "remedial_students",
        "batch_attendance",        # Must be before remedial_batches and students (FK refs)
        "student_feedback",        # References remedial_batches (NO ACTION) and students
        "peer_mentors",            # References remedial_batches (NO ACTION) and students
        "extra_class_requests",    # References remedial_batches (NO ACTION), subjects, students
        "teacher_reports",
        "attendance",
        "semester_attendance",
        "student_backlog_subjects",
        "results",
        "remedial_batches",
        "teachers",
        "students",
        "classrooms",
        "subjects",
        "notifications",
        "audit_logs",
    ]
    
    try:
        for table in tables_to_clear:
            print(f"Deleting from {table}...")
            conn.execute(f"DELETE FROM {table}")
        
        # Ensure Super Admin exists and is active
        if super_admin_id:
            conn.execute("UPDATE users SET is_active = 1 WHERE id = ?", (super_admin_id["id"],))
        else:
            from werkzeug.security import generate_password_hash
            conn.execute(
                "INSERT INTO users (username, password_hash, role, full_name, department_id, is_active) VALUES (?, ?, 'super_admin', 'Super Admin', NULL, 1)",
                ("superadmin", generate_password_hash("admin123"),)
            )
        
        conn.commit()
        print("Database cleared successfully!")
        
        # Verify counts after
        student_count = conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"]
        teacher_count = conn.execute("SELECT COUNT(*) AS c FROM teachers").fetchone()["c"]
        batch_count = conn.execute("SELECT COUNT(*) AS c FROM remedial_batches").fetchone()["c"]
        print(f"After clear: {student_count} students, {teacher_count} teachers, {batch_count} batches")
        
    except Exception as e:
        conn.rollback()
        print(f"Error clearing database: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    test_clear_database()