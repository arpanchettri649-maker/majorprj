import sqlite3

conn = sqlite3.connect(r'd:\major project 14 oct\instance\remedial.db')
c = conn.cursor()
c.execute("""
    SELECT u.username, u.email, u.role, s.email as student_email, t.email as teacher_email 
    FROM users u 
    LEFT JOIN students s ON s.user_id = u.id 
    LEFT JOIN teachers t ON t.user_id = u.id
""")
rows = c.fetchall()
for r in rows:
    print(f"Username: {r[0]}, Users.email: {r[1]}, Role: {r[2]}, Student.email: {r[3]}, Teacher.email: {r[4]}")