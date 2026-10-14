from auth import hash_password
from db import get_db, init_db

DEPARTMENTS = [
    ("CO", "Computer Engineering"),
    ("ME", "Mechanical Engineering"),
    ("MT", "Mechatronics"),
    ("CE", "Civil Engineering"),
    ("MC", "Medical Laboratory Technology"),
]

DEPT_ADMINS = [
    ("coadmin", "CO Admin", "CO"),
    ("meadmin", "ME Admin", "ME"),
    ("mtadmin", "MT Admin", "MT"),
    ("ceadmin", "CE Admin", "CE"),
    ("mcadmin", "MC Admin", "MC"),
]

TEACHERS = [
    ("COT01", "Mr. Ram Sharma", "CO"),
    ("COT02", "Ms. Sita Gurung", "CO"),
    ("MET01", "Mr. Hari Thapa", "ME"),
    ("MTT01", "Ms. Anjali Rai", "MT"),
    ("CET01", "Mr. Bijay Tamang", "CE"),
    ("MCT01", "Ms. Puja Shrestha", "MC"),
]

ROOMS = [
    ("LAB-CO-1", 40, "CO"),
    ("LAB-ME-1", 35, "ME"),
    ("LAB-MT-1", 30, "MT"),
    ("LAB-CE-1", 35, "CE"),
    ("LAB-MC-1", 25, "MC"),
    ("HALL-A", 60, None),
]


def seed():
    init_db()
    with get_db() as conn:
        if conn.execute("SELECT COUNT(*) AS c FROM departments").fetchone()["c"]:
            print("Database already seeded.")
            return

        for code, name in DEPARTMENTS:
            conn.execute(
                "INSERT INTO departments (code, name) VALUES (?, ?)", (code, name)
            )

        conn.execute(
            """
            INSERT INTO users (username, password_hash, role, full_name)
            VALUES (?, ?, 'super_admin', 'Super Administrator')
            """,
            ("superadmin", hash_password("admin123")),
        )

        dept_map = {
            row["code"]: row["id"]
            for row in conn.execute("SELECT id, code FROM departments").fetchall()
        }

        for username, name, dept_code in DEPT_ADMINS:
            conn.execute(
                """
                INSERT INTO users
                (username, password_hash, role, full_name, department_id)
                VALUES (?, ?, 'dept_admin', ?, ?)
                """,
                (username, hash_password("admin123"), name, dept_map[dept_code]),
            )

        for eid, name, dept_code in TEACHERS:
            dept_id = dept_map[dept_code]
            cur = conn.execute(
                "INSERT INTO teachers (employee_id, full_name, department_id) VALUES (?, ?, ?)",
                (eid, name, dept_id),
            )
            username = eid.lower()
            conn.execute(
                """
                INSERT INTO users
                (username, password_hash, role, full_name, department_id)
                VALUES (?, ?, 'teacher', ?, ?)
                """,
                (username, hash_password("teacher123"), name, dept_id),
            )
            user_id = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username,)
            ).fetchone()["id"]
            conn.execute(
                "UPDATE teachers SET user_id = ? WHERE id = ?",
                (user_id, cur.lastrowid),
            )

        for room_code, capacity, dept_code in ROOMS:
            dept_id = dept_map.get(dept_code) if dept_code else None
            conn.execute(
                "INSERT INTO classrooms (room_code, capacity, department_id) VALUES (?, ?, ?)",
                (room_code, capacity, dept_id),
            )

        subjects = [
            ("MATH201", "Engineering Mathematics", "CO"),
            ("PROG101", "Programming Fundamentals", "CO"),
            ("PHY101", "Applied Physics", "ME"),
            ("DRAW101", "Engineering Drawing", "CE"),
        ]
        for code, name, dept_code in subjects:
            conn.execute(
                "INSERT INTO subjects (code, name, department_id) VALUES (?, ?, ?)",
                (code, name, dept_map[dept_code]),
            )

        conn.commit()
        print("Seed data created successfully.")
        print("Super Admin: superadmin / admin123")
        print("Dept Admins: coadmin, meadmin, mtadmin, ceadmin, mcadmin / admin123")
        print("Teachers: cot01, met01, etc. / teacher123")


if __name__ == "__main__":
    seed()
