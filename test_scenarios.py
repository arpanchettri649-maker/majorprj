"""
End-to-end verification script for the 8 required scenarios:
1. Passed all subjects but low attendance -> appears in Weak Student List.
2. Good attendance but failed 2 subjects -> appears with both subjects displayed, Backlog reason.
3. Low attendance and failed subjects -> appears with reason 'Both'.
4. Passed all subjects and sufficient attendance -> does NOT appear in Weak Student List.
5. Mentor marks attendance -> saved properly, attendance percentage updates immediately.
6. ATTC Backlog Analysis reflects latest attendance and academic records.
7. Dept Admin dashboard has only ATTC Backlog Analysis, Upload Result & Backlog removed.
8. System works correctly with fresh records after reset.
"""

import unittest
from app import create_app
from db import get_db, set_setting, get_setting
import ai_engine
from clear_data import reset_database

class TestATTCBacklogSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reset database to fresh clean state
        reset_database()
        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.client = cls.app.test_client()

    def test_01_to_04_weak_student_identification(self):
        """Scenarios 1-4: Student weak classification rules."""
        print("\n--- Verifying Scenarios 1 to 4: Weak Student Identification Rules ---")
        with get_db() as conn:
            # Set minimum attendance threshold to 75.0%
            set_setting("min_attendance_threshold", "75", conn=conn)

            # Get CO subjects
            sub1 = conn.execute("SELECT id, code, name FROM subjects WHERE department_id = 1 AND code = 'MATH201'").fetchone()
            sub2 = conn.execute("SELECT id, code, name FROM subjects WHERE department_id = 1 AND code = 'PROG101'").fetchone()

            # Insert Student 1 (TEST01): Low attendance (60%), Passed all subjects
            c1 = conn.execute("""
                INSERT INTO students (student_id, full_name, department_id, semester, section, academic_batch, cgpa)
                VALUES ('TEST01', 'Student LowAtt', 1, 3, 'A', '2024-2027', 7.5)
            """)
            s1_id = c1.lastrowid
            # Passed result
            conn.execute("""
                INSERT INTO results (student_id, student_name, department_id, semester, section, subject_id, marks, pass_marks, result, academic_batch)
                VALUES ('TEST01', 'Student LowAtt', 1, 3, 'A', ?, 65, 40, 'PASS', '2024-2027')
            """, (sub1["id"],))
            # 60% Attendance
            conn.execute("""
                INSERT INTO semester_attendance (student_id, semester, academic_batch, subject_id, classes_attended, total_classes, attendance_percent)
                VALUES (?, 3, '2024-2027', ?, 12, 20, 60.0)
            """, (s1_id, sub1["id"]))

            # Insert Student 2 (TEST02): Good attendance (85%), Failed 2 subjects
            c2 = conn.execute("""
                INSERT INTO students (student_id, full_name, department_id, semester, section, academic_batch, cgpa)
                VALUES ('TEST02', 'Student FailTwo', 1, 3, 'A', '2024-2027', 5.0)
            """)
            s2_id = c2.lastrowid
            # 2 Fails
            conn.execute("""
                INSERT INTO results (student_id, student_name, department_id, semester, section, subject_id, marks, pass_marks, result, academic_batch)
                VALUES ('TEST02', 'Student FailTwo', 1, 3, 'A', ?, 25, 40, 'FAIL', '2024-2027')
            """, (sub1["id"],))
            conn.execute("""
                INSERT INTO results (student_id, student_name, department_id, semester, section, subject_id, marks, pass_marks, result, academic_batch)
                VALUES ('TEST02', 'Student FailTwo', 1, 3, 'A', ?, 20, 40, 'FAIL', '2024-2027')
            """, (sub2["id"],))
            # 85% Attendance
            conn.execute("""
                INSERT INTO semester_attendance (student_id, semester, academic_batch, subject_id, classes_attended, total_classes, attendance_percent)
                VALUES (?, 3, '2024-2027', ?, 17, 20, 85.0)
            """, (s2_id, sub1["id"]))

            # Insert Student 3 (TEST03): Low attendance (50%) AND Failed 1 subject
            c3 = conn.execute("""
                INSERT INTO students (student_id, full_name, department_id, semester, section, academic_batch, cgpa)
                VALUES ('TEST03', 'Student Both', 1, 3, 'A', '2024-2027', 4.5)
            """)
            s3_id = c3.lastrowid
            # 1 Fail
            conn.execute("""
                INSERT INTO results (student_id, student_name, department_id, semester, section, subject_id, marks, pass_marks, result, academic_batch)
                VALUES ('TEST03', 'Student Both', 1, 3, 'A', ?, 30, 40, 'FAIL', '2024-2027')
            """, (sub1["id"],))
            # 50% Attendance
            conn.execute("""
                INSERT INTO semester_attendance (student_id, semester, academic_batch, subject_id, classes_attended, total_classes, attendance_percent)
                VALUES (?, 3, '2024-2027', ?, 10, 20, 50.0)
            """, (s3_id, sub1["id"]))

            # Insert Student 4 (TEST04): Good attendance (90%) AND Passed all subjects
            c4 = conn.execute("""
                INSERT INTO students (student_id, full_name, department_id, semester, section, academic_batch, cgpa)
                VALUES ('TEST04', 'Student GoodPass', 1, 3, 'A', '2024-2027', 8.5)
            """)
            s4_id = c4.lastrowid
            # Passed
            conn.execute("""
                INSERT INTO results (student_id, student_name, department_id, semester, section, subject_id, marks, pass_marks, result, academic_batch)
                VALUES ('TEST04', 'Student GoodPass', 1, 3, 'A', ?, 80, 40, 'PASS', '2024-2027')
            """, (sub1["id"],))
            # 90% Attendance
            conn.execute("""
                INSERT INTO semester_attendance (student_id, semester, academic_batch, subject_id, classes_attended, total_classes, attendance_percent)
                VALUES (?, 3, '2024-2027', ?, 18, 20, 90.0)
            """, (s4_id, sub1["id"]))

            conn.commit()

            # Run ai_engine.identify_weak_students with weak_only=True for Weak Student List
            weak_students = ai_engine.identify_weak_students(1, min_attendance_threshold=75.0, conn=conn, weak_only=True)
            weak_rolls = {st["roll_number"]: st for st in weak_students}

            # 1. TEST01 (Passed but low attendance)
            self.assertIn("TEST01", weak_rolls, "Scenario 1: Low attendance passed student must appear in Weak Student List")
            st1 = weak_rolls["TEST01"]
            self.assertEqual(st1["academic_result_status"], "Pass")
            self.assertEqual(st1["reason_for_selection"], "Low Attendance")
            self.assertEqual(st1["total_backlogs"], 0)
            print("Scenario 1 PASSED: Low attendance student appears with Pass status and 'Low Attendance' reason.")

            # 2. TEST02 (Good attendance but failed 2 subjects)
            self.assertIn("TEST02", weak_rolls, "Scenario 2: Failed 2 subjects must appear in Weak Student List")
            st2 = weak_rolls["TEST02"]
            self.assertEqual(st2["academic_result_status"], "Fail")
            self.assertEqual(st2["reason_for_selection"], "Backlog")
            self.assertEqual(st2["total_backlogs"], 2)
            self.assertIn("Engineering Mathematics", st2["failed_subjects"])
            self.assertIn("Programming Fundamentals", st2["failed_subjects"])
            print("Scenario 2 PASSED: Student with 2 backlogs appears with Fail status and both subjects listed.")

            # 3. TEST03 (Low attendance AND failed subjects)
            self.assertIn("TEST03", weak_rolls, "Scenario 3: Low attendance AND fail must appear in Weak Student List")
            st3 = weak_rolls["TEST03"]
            self.assertEqual(st3["academic_result_status"], "Fail")
            self.assertEqual(st3["reason_for_selection"], "Both")
            self.assertEqual(st3["total_backlogs"], 1)
            print("Scenario 3 PASSED: Student with both low attendance and backlog identified with reason 'Both'.")

            # 4. TEST04 (Passed and good attendance)
            self.assertNotIn("TEST04", weak_rolls, "Scenario 4: Passed student with good attendance must NOT appear in Weak Student List")
            print("Scenario 4 PASSED: Student with good attendance and pass status excluded from Weak Student List.")

    def test_05_and_06_mentor_marking_and_dynamic_analysis(self):
        """Scenarios 5 & 6: Mentor marks attendance, updates percentages immediately, ATTC reflects live."""
        print("\n--- Verifying Scenarios 5 & 6: Mentor Marks Attendance & Dynamic Analysis ---")
        with get_db() as conn:
            teacher = conn.execute("SELECT id, user_id FROM teachers WHERE user_id = (SELECT id FROM users WHERE username = 'cot01')").fetchone()
            sub1 = conn.execute("SELECT id FROM subjects WHERE code = 'MATH201'").fetchone()
            s2 = conn.execute("SELECT id FROM students WHERE student_id = 'TEST02'").fetchone()

            # Create remedial batch for TEST02 (who failed MATH201)
            c_batch = conn.execute("""
                INSERT INTO remedial_batches (batch_code, subject_id, teacher_id, department_id, semester, section, classroom_id, status, academic_batch)
                VALUES ('REM-TEST-01', ?, ?, 1, 3, 'A', 1, 'SCHEDULED', '2024-2027')
            """, (sub1["id"], teacher["id"]))
            batch_id = c_batch.lastrowid

            conn.execute("""
                INSERT INTO remedial_students (batch_id, student_id)
                VALUES (?, ?)
            """, (batch_id, s2["id"]))
            conn.commit()

        # Login as teacher cot01
        with self.client:
            login_resp = self.client.post("/login", data={"username": "cot01", "password": "teacher123"}, follow_redirects=True)
            self.assertEqual(login_resp.status_code, 200)

            # View batch detail
            batch_res = self.client.get(f"/teacher/batch/{batch_id}")
            self.assertEqual(batch_res.status_code, 200)

            # Mark attendance for date 2026-10-14 as PRESENT
            post_res = self.client.post(f"/teacher/batch/{batch_id}", data={
                "action": "save_attendance",
                "attendance_date": "2026-10-14",
                f"status_{s2['id']}": "PRESENT"
            }, follow_redirects=True)
            self.assertEqual(post_res.status_code, 200)

            # Check database: record saved in both attendance and batch_attendance
            with get_db() as conn:
                att_row = conn.execute("SELECT * FROM attendance WHERE batch_id = ? AND student_id = ?", (batch_id, s2["id"])).fetchone()
                self.assertIsNotNone(att_row, "Attendance record must be saved in attendance table")
                self.assertEqual(att_row["status"], "PRESENT")

                batch_att_row = conn.execute("SELECT * FROM batch_attendance WHERE batch_id = ? AND student_id = ?", (batch_id, s2["id"])).fetchone()
                self.assertIsNotNone(batch_att_row, "Attendance record must be saved in batch_attendance table")
                self.assertEqual(batch_att_row["status"], "PRESENT")

                # Verify student's total attended classes updated:
                # Previous semester_attendance was 17/20 (85.0%).
                # Now mentor added 1 PRESENT out of 1 remedial class -> Total = 18/21 = 85.7%.
                analysis = ai_engine.get_attc_backlog_analysis(department_id=1, min_attendance_threshold=75.0, conn=conn)
                st2_eval = next((s for s in analysis["weak_students"] if s["roll_number"] == "TEST02"), None)
                self.assertIsNotNone(st2_eval)
                self.assertEqual(st2_eval["attendance_percentage"], "85.7%")
                self.assertIn("REM-TEST-01", st2_eval["remedial_class_status"])

            print("Scenario 5 PASSED: Mentor marked attendance successfully; database saved; percentage updated immediately to 85.7%.")

        # Scenario 6: ATTC Backlog Analysis endpoint check
        with self.client:
            self.client.post("/login", data={"username": "coadmin", "password": "admin123"}, follow_redirects=True)
            analysis_res = self.client.get("/admin/attc-backlog-analysis")
            self.assertEqual(analysis_res.status_code, 200)
            analysis_html = analysis_res.data.decode("utf-8")
            self.assertIn("Upload Result", analysis_html)
            self.assertIn("TEST01", analysis_html)
            self.assertIn("TEST02", analysis_html)
            self.assertIn("TEST03", analysis_html)
            self.assertNotIn("TEST04", analysis_html)
            print("Scenario 6 PASSED: ATTC Backlog Analysis renders dynamically from actual database records.")

    def test_07_department_admin_dashboard_navigation(self):
        """Scenario 7: Dept Admin dashboard has only ATTC Backlog Analysis, no Upload Result/Backlog."""
        print("\n--- Verifying Scenario 7: Department Admin Dashboard Navigation ---")
        with self.client:
            # Login as coadmin
            res = self.client.post("/login", data={"username": "coadmin", "password": "admin123"}, follow_redirects=True)
            self.assertEqual(res.status_code, 200)

            # Check dashboard html
            dash_res = self.client.get("/admin/dashboard")
            self.assertEqual(dash_res.status_code, 200)
            dash_html = dash_res.data.decode("utf-8")

            # Must NOT contain "Upload Result" or "Upload Backlog"
            self.assertNotIn("Upload Result", dash_html)
            self.assertNotIn("Upload Backlog", dash_html)
            self.assertNotIn("/admin/upload-results", dash_html)
            self.assertNotIn("/admin/upload-backlogs", dash_html)

            # MUST contain "ATTC Backlog Analysis"
            self.assertIn("Upload Result", dash_html)
            self.assertIn("/admin/attc-backlog-analysis", dash_html)

            # Old upload URLs must redirect to attc-backlog-analysis
            r1 = self.client.get("/admin/upload-results", follow_redirects=False)
            self.assertEqual(r1.status_code, 302)
            self.assertIn("/admin/attc-backlog-analysis", r1.headers["Location"])

            r2 = self.client.get("/admin/upload-backlogs", follow_redirects=False)
            self.assertEqual(r2.status_code, 302)
            self.assertIn("/admin/attc-backlog-analysis", r2.headers["Location"])
            print("Scenario 7 PASSED: Upload Result/Backlog removed, ATTC Backlog Analysis is central.")

    def test_08_fresh_database_consistency(self):
        """Scenario 8: System works cleanly with fresh database records."""
        print("\n--- Verifying Scenario 8: Clean Database Integrity ---")
        with get_db() as conn:
            # Check users
            users = conn.execute("SELECT username, role FROM users WHERE username IN ('superadmin', 'coadmin', 'cot01')").fetchall()
            self.assertEqual(len(users), 3)

            # Check threshold config
            thresh = get_setting("min_attendance_threshold", conn=conn)
            self.assertIsNotNone(thresh)

        print("Scenario 8 PASSED: System integrity verified with fresh database state.")

if __name__ == "__main__":
    unittest.main()
