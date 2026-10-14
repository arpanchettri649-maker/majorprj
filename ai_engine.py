from collections import defaultdict
from datetime import datetime, timedelta

from academic import LOW_ATTENDANCE_PERCENT
from db import add_notification, get_db, get_setting

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
SLOTS = [
    ("15:30", "16:30"),  # Remedial class after college: 3:30 PM – 4:30 PM
]
SLOT_END = {
    "15:30": "16:30",
    "09:00": "10:00",
    "10:15": "11:15",
    "11:30": "12:30",
    "14:00": "15:00",
    "15:15": "16:15",
}
EXTRA_CLASS_SLOT = ("15:30", "16:30")  # After college: 3:30 PM – 4:30 PM


def normalize_time(value):
    """Normalize time strings to HH:MM for consistent grid matching."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return text
    parts = text.split(":")
    if len(parts) >= 2:
        try:
            return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
        except ValueError:
            return text
    return text


def detect_failures_and_build_batches(department_id=None, conn=None):
    def _execute(c):
        # 1. Gather all student failures from results table
        res_query = """
            SELECT r.department_id, r.semester, r.subject_id, r.section, r.academic_batch,
                   s.code AS subject_code, s.name AS subject_name, st.id AS student_pk
            FROM results r
            JOIN subjects s ON s.id = r.subject_id
            JOIN students st ON UPPER(st.student_id) = UPPER(r.student_id)
            WHERE r.result = 'FAIL'
        """
        params = []
        if department_id:
            res_query += " AND r.department_id = ?"
            params.append(department_id)
        res_failures = c.execute(res_query, params).fetchall()

        # 2. Gather student backlog subjects from student_backlog_subjects table
        backlog_query = """
            SELECT st.department_id, sbs.original_semester AS semester, sbs.subject_id,
                   st.section, st.academic_batch, s.code AS subject_code, s.name AS subject_name,
                   st.id AS student_pk
            FROM student_backlog_subjects sbs
            JOIN subjects s ON s.id = sbs.subject_id
            JOIN students st ON st.id = sbs.student_id
            WHERE sbs.status != 'CLEARED'
        """
        b_params = []
        if department_id:
            backlog_query += " AND st.department_id = ?"
            b_params.append(department_id)
        backlog_failures = c.execute(backlog_query, b_params).fetchall()

        # 3. Consider low attendance students per subject from semester_attendance
        threshold_val = float(get_setting("min_attendance_threshold", 75, conn=c))
        att_query = """
            SELECT st.department_id, sa.semester, sa.subject_id, st.section, st.academic_batch,
                   s.code AS subject_code, s.name AS subject_name, st.id AS student_pk
            FROM semester_attendance sa
            JOIN subjects s ON s.id = sa.subject_id
            JOIN students st ON st.id = sa.student_id
            WHERE sa.subject_id IS NOT NULL AND sa.attendance_percent < ?
        """
        att_params = [threshold_val]
        if department_id:
            att_query += " AND st.department_id = ?"
            att_params.append(department_id)
        low_att_records = c.execute(att_query, att_params).fetchall()

        # Combine all records and group by (dept, semester, subject, section, batch)
        all_records = list(res_failures) + list(backlog_failures) + list(low_att_records)

        groups = defaultdict(dict)
        for row in all_records:
            key = (
                row["department_id"],
                row["semester"],
                row["subject_id"],
                row["section"],
                row["academic_batch"] or "",
            )
            student_pk = row["student_pk"]
            groups[key][student_pk] = row

        created = 0
        allocated_students = 0
        for (dept_id, semester, subject_id, section, academic_batch), student_dict in groups.items():
            first_row = next(iter(student_dict.values()))
            subject_code = first_row["subject_code"]
            batch_tag = (academic_batch or "NA").replace("-", "")
            batch_code = f"RB-{subject_code}-S{semester}-{section}-{batch_tag}"
            batch = c.execute(
                "SELECT id FROM remedial_batches WHERE batch_code = ?",
                (batch_code,),
            ).fetchone()
            if not batch:
                cur = c.execute(
                    """
                    INSERT INTO remedial_batches
                    (batch_code, department_id, subject_id, semester, section, academic_batch, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
                    """,
                    (batch_code, dept_id, subject_id, semester, section, academic_batch or None),
                )
                batch_id = cur.lastrowid
                created += 1
            else:
                batch_id = batch["id"]

            for student_pk in student_dict.keys():
                cur = c.execute(
                    """
                    INSERT OR IGNORE INTO remedial_students (batch_id, student_id)
                    VALUES (?, ?)
                    """,
                    (batch_id, student_pk),
                )
                if cur.rowcount > 0:
                    allocated_students += 1
        return created

    if conn is not None:
        return _execute(conn)
    with get_db() as c:
        res = _execute(c)
        c.commit()
        return res


def get_available_teachers(department_id, conn):
    """Return teachers in department ordered by lowest remedial teaching load."""
    rows = conn.execute(
        """
        SELECT t.*, (
            SELECT COUNT(*) FROM remedial_batches rb
            WHERE rb.teacher_id = t.id AND rb.status IN ('SCHEDULED', 'IN_PROGRESS')
        ) AS current_load
        FROM teachers t
        WHERE t.department_id = ? OR ? IS NULL
        ORDER BY current_load ASC, t.id ASC
        """,
        (department_id, department_id),
    ).fetchall()
    return [dict(r) for r in rows]


def get_available_classrooms(department_id, student_count, conn):
    """Return matching classrooms that can accommodate the student count."""
    rows = conn.execute(
        """
        SELECT *
        FROM classrooms
        WHERE (department_id = ? OR department_id IS NULL) AND capacity >= ?
        ORDER BY capacity ASC, id ASC
        """,
        (department_id, student_count),
    ).fetchall()
    return [dict(r) for r in rows]


def generate_timetable(department_id=None, conn=None):
    def _execute(c):
        query = """
            SELECT rb.*, COUNT(rs.id) AS student_count
            FROM remedial_batches rb
            LEFT JOIN remedial_students rs ON rs.batch_id = rb.id
            WHERE rb.status = 'PENDING'
        """
        params = []
        if department_id:
            query += " AND rb.department_id = ?"
            params.append(department_id)
        query += " GROUP BY rb.id ORDER BY rb.id ASC"
        batches = c.execute(query, params).fetchall()

        # Pre-populate busy sets from all existing timetable entries in DB to avoid clashes
        c.execute(
            "UPDATE timetable SET start_time = '15:30', end_time = '16:30' WHERE start_time != '15:30' OR end_time != '16:30'"
        )
        existing_entries = c.execute(
            """
            SELECT tt.teacher_id, tt.classroom_id, tt.day_of_week, tt.start_time,
                   rb.semester, rb.department_id
            FROM timetable tt
            JOIN remedial_batches rb ON rb.id = tt.batch_id
            """
        ).fetchall()
        teacher_busy = {(r["teacher_id"], r["day_of_week"], r["start_time"]) for r in existing_entries}
        room_busy = {(r["classroom_id"], r["day_of_week"], r["start_time"]) for r in existing_entries}
        # Constraint: Do not conduct same semester classes on the same day
        semester_day_busy = {(r["department_id"], r["semester"], r["day_of_week"]) for r in existing_entries}

        scheduled = 0

        for batch in batches:
            dept_id = batch["department_id"]
            batch_semester = batch["semester"]
            student_count = max(1, batch["student_count"] or 1)
            teachers = get_available_teachers(dept_id, c)
            rooms = get_available_classrooms(dept_id, student_count, c)

            if not teachers or not rooms:
                continue

            slot_found = False
            for day in DAYS:
                # Do not conduct same semester classes on the same day
                if (dept_id, batch_semester, day) in semester_day_busy:
                    continue

                for start, end in SLOTS:
                    # Find a teacher and room pair that are both free for this slot
                    free_teacher = None
                    for t in teachers:
                        if (t["id"], day, start) not in teacher_busy:
                            free_teacher = t
                            break
                    if not free_teacher:
                        continue

                    free_room = None
                    for r in rooms:
                        if (r["id"], day, start) not in room_busy:
                            free_room = r
                            break
                    if not free_room:
                        continue

                    # Allocate timetable entry
                    c.execute(
                        """
                        INSERT INTO timetable
                        (batch_id, day_of_week, start_time, end_time, classroom_id, teacher_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (batch["id"], day, normalize_time(start), normalize_time(end), free_room["id"], free_teacher["id"]),
                    )
                    c.execute(
                        """
                        UPDATE remedial_batches
                        SET teacher_id = ?, classroom_id = ?, status = 'SCHEDULED'
                        WHERE id = ?
                        """,
                        (free_teacher["id"], free_room["id"], batch["id"]),
                    )
                    teacher_busy.add((free_teacher["id"], day, start))
                    room_busy.add((free_room["id"], day, start))
                    semester_day_busy.add((dept_id, batch_semester, day))
                    slot_found = True
                    scheduled += 1

                    if free_teacher.get("user_id"):
                        add_notification(
                            free_teacher["user_id"],
                            f"New remedial class assigned: {batch['batch_code']} on {day} {start}–{end} ({free_room['room_code']})",
                            conn=c,
                        )
                    break
                if slot_found:
                    break
        return scheduled

    if conn is not None:
        return _execute(conn)
    with get_db() as c:
        res = _execute(c)
        c.commit()
        return res


def get_analytics(department_id=None):
    with get_db() as conn:
        params = [department_id] if department_id else []
        dept_clause = "AND department_id = ?" if department_id else ""
        dept_where = "WHERE department_id = ?" if department_id else ""

        total_failures = conn.execute(
            f"SELECT COUNT(*) AS c FROM results WHERE result = 'FAIL' {dept_clause}",
            params,
        ).fetchone()["c"]

        total_batches = conn.execute(
            f"SELECT COUNT(*) AS c FROM remedial_batches {dept_where}",
            params,
        ).fetchone()["c"]

        pending_batches = conn.execute(
            f"SELECT COUNT(*) AS c FROM remedial_batches WHERE status = 'PENDING' {dept_clause}",
            params,
        ).fetchone()["c"]

        scheduled_batches = conn.execute(
            f"SELECT COUNT(*) AS c FROM remedial_batches WHERE status IN ('SCHEDULED', 'IN_PROGRESS') {dept_clause}",
            params,
        ).fetchone()["c"]

        completed_batches = conn.execute(
            f"SELECT COUNT(*) AS c FROM remedial_batches WHERE status = 'COMPLETED' {dept_clause}",
            params,
        ).fetchone()["c"]

        difficult = conn.execute(
            f"""
            SELECT s.name, s.code, COUNT(*) AS fail_count
            FROM results r
            JOIN subjects s ON s.id = r.subject_id
            WHERE r.result = 'FAIL' {dept_clause.replace('department_id', 'r.department_id') if department_id else ''}
            GROUP BY s.id
            ORDER BY fail_count DESC
            LIMIT 6
            """,
            params,
        ).fetchall()

        return {
            "total_failures": total_failures,
            "total_batches": total_batches,
            "pending_batches": pending_batches,
            "scheduled_batches": scheduled_batches,
            "completed_batches": completed_batches,
            "difficult_subjects": [dict(r) for r in difficult],
        }


def get_upcoming_classes(teacher_id, days=7):
    today = datetime.now()
    end = today + timedelta(days=days)
    day_names = {d: i for i, d in enumerate(DAYS)}
    today_idx = today.weekday()

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT t.*, rb.batch_code, s.name AS subject_name, c.room_code,
                   te.full_name AS teacher_name
            FROM timetable t
            JOIN remedial_batches rb ON rb.id = t.batch_id
            JOIN subjects s ON s.id = rb.subject_id
            JOIN classrooms c ON c.id = t.classroom_id
            JOIN teachers te ON te.id = t.teacher_id
            WHERE t.teacher_id = ?
            ORDER BY t.day_of_week, t.start_time
            """,
            (teacher_id,),
        ).fetchall()

    upcoming = []
    for row in rows:
        day_str = row["day_of_week"]
        slot_day = day_names.get(day_str, 0)
        days_ahead = (slot_day - today_idx) % 7
        if days_ahead == 0 and today.strftime("%H:%M") > row["start_time"]:
            days_ahead = 7
        class_date = today.date() + timedelta(days=days_ahead)
        if class_date <= end.date():
            item = dict(row)
            item["class_date"] = class_date.strftime("%Y-%m-%d")
            upcoming.append(item)
    upcoming.sort(key=lambda x: (x["class_date"], x["start_time"]))
    return upcoming


def identify_weak_students(department_id=None, min_attendance_threshold=None, conn=None, weak_only=False):
    """
    Identify weak students based on actual database records.
    Weak Student = Low Attendance and/or Backlog Subjects
    Returns student records with the 9 required fields:
    1. student_name
    2. roll_number
    3. semester
    4. attendance_percentage
    5. academic_result_status (Pass/Fail)
    6. failed_subjects
    7. total_backlogs
    8. reason_for_selection (Low Attendance, Backlog, or Both)
    9. remedial_class_status
    Plus metadata (is_weak, has_low_attendance, has_backlogs, etc.)
    """
    def _execute(c):
        threshold_val = float(min_attendance_threshold) if min_attendance_threshold is not None else float(
            get_setting("min_attendance_threshold", 75, conn=c)
        )

        dept_filter = "WHERE (? IS NULL OR s.department_id = ?)"
        params = [department_id, department_id]

        students = c.execute(f"""
            SELECT s.id, s.student_id, s.full_name, s.department_id, s.semester, s.section,
                   s.academic_batch, s.cgpa, d.code AS dept_code, d.name AS dept_name
            FROM students s
            JOIN departments d ON d.id = s.department_id
            {dept_filter}
            ORDER BY s.semester ASC, s.student_id ASC
        """, params).fetchall()

        evaluated = []
        for s in students:
            # 1. Backlogs / failed subjects - prioritize student_backlog_subjects table for accuracy
            backlog_rows = c.execute("""
                SELECT DISTINCT sub.id AS subject_id, sub.code AS subject_code, sub.name AS subject_name,
                       sbs.original_semester, sbs.status, sbs.year_back_status
                FROM student_backlog_subjects sbs
                JOIN subjects sub ON sub.id = sbs.subject_id
                WHERE sbs.student_id = ? AND sbs.status != 'CLEARED'
                ORDER BY sub.name
            """, (s["id"],)).fetchall()

            # Also check results table for any FAIL records not in backlog table
            result_fail_rows = c.execute("""
                SELECT DISTINCT sub.id AS subject_id, sub.code AS subject_code, sub.name AS subject_name
                FROM results r
                JOIN subjects sub ON sub.id = r.subject_id
                WHERE UPPER(r.student_id) = UPPER(?) AND r.result = 'FAIL'
                ORDER BY sub.name
            """, (s["student_id"],)).fetchall()

            # Combine both sources, avoiding duplicates
            seen_subjects = {r["subject_id"] for r in backlog_rows}
            failed_rows = list(backlog_rows)
            for r in result_fail_rows:
                if r["subject_id"] not in seen_subjects:
                    failed_rows.append(r)
                    seen_subjects.add(r["subject_id"])

            has_backlogs = len(failed_rows) > 0
            total_backlogs = len(failed_rows)
            academic_result_status = "Fail" if has_backlogs else "Pass"
            failed_subjects_list = [f"{r['subject_name']} ({r['subject_code']})" for r in failed_rows]
            failed_subjects_str = ", ".join(failed_subjects_list) if failed_subjects_list else "—"

            # 2. Actual attendance records
            att_rows = c.execute("""
                SELECT status FROM (
                    SELECT batch_id, attendance_date, status FROM attendance WHERE student_id = ?
                    UNION
                    SELECT batch_id, attendance_date, status FROM batch_attendance WHERE student_id = ?
                )
            """, (s["id"], s["id"])).fetchall()

            mentor_total = len(att_rows)
            mentor_present = sum(1 for r in att_rows if r["status"] == "PRESENT")

            sem_row = c.execute("""
                SELECT SUM(classes_attended) AS attended, SUM(total_classes) AS total, AVG(attendance_percent) AS avg_pct
                FROM semester_attendance
                WHERE student_id = ?
            """, (s["id"],)).fetchone()

            sem_total = sem_row["total"] or 0
            sem_present = sem_row["attended"] or 0
            sem_avg_pct = sem_row["avg_pct"]

            total_classes = mentor_total + sem_total
            total_attended = mentor_present + sem_present

            if total_classes > 0:
                attendance_percent = round((total_attended / total_classes) * 100, 1)
            elif sem_avg_pct is not None:
                attendance_percent = round(sem_avg_pct, 1)
            else:
                attendance_percent = None

            has_low_attendance = (attendance_percent is not None and attendance_percent < threshold_val)

            # 3. Weak student rule
            is_weak = has_backlogs or has_low_attendance

            if has_low_attendance and has_backlogs:
                reason = "Both"
            elif has_low_attendance:
                reason = "Low Attendance"
            elif has_backlogs:
                reason = "Backlog"
            else:
                reason = "None"

            # 4. Remedial Class Status
            batch_allocations = c.execute("""
                SELECT rb.batch_code, rb.status, sub.name AS subject_name
                FROM remedial_students rs
                JOIN remedial_batches rb ON rb.id = rs.batch_id
                JOIN subjects sub ON sub.id = rb.subject_id
                WHERE rs.student_id = ?
            """, (s["id"],)).fetchall()

            if batch_allocations:
                batch_codes = [b["batch_code"] for b in batch_allocations]
                remedial_class_status = f"Allocated ({', '.join(batch_codes)})"
            else:
                if is_weak:
                    remedial_class_status = "Eligible - Pending Allocation"
                else:
                    remedial_class_status = "Not Required"

            evaluated.append({
                "id": s["id"],
                "student_id": s["student_id"],
                "roll_number": s["student_id"],
                "student_name": s["full_name"],
                "full_name": s["full_name"],
                "semester": s["semester"],
                "section": s["section"],
                "academic_batch": s["academic_batch"],
                "department_id": s["department_id"],
                "dept_code": s["dept_code"],
                "cgpa": s["cgpa"],
                "attendance_percent": attendance_percent,
                "attendance_percentage": f"{attendance_percent:.1f}%" if attendance_percent is not None else "N/A",
                "academic_result_status": academic_result_status,
                "failed_subjects": failed_subjects_str,
                "failed_subjects_list": failed_subjects_list,
                "total_backlogs": total_backlogs,
                "reason_for_selection": reason,
                "remedial_class_status": remedial_class_status,
                "has_low_attendance": has_low_attendance,
                "has_backlogs": has_backlogs,
                "is_weak": is_weak,
                "weakness_reason": reason if is_weak else "",
                "total_classes": total_classes,
                "total_attended": total_attended,
            })

        if weak_only:
            return [s for s in evaluated if s["is_weak"]]
        return evaluated

    if conn is not None:
        return _execute(conn)
    with get_db() as c:
        return _execute(c)


def get_attc_backlog_analysis(department_id=None, min_attendance_threshold=None, conn=None):
    """
    Central ATTC Backlog Analysis logic:
    Combines academic results and attendance to identify students requiring remedial classes.
    """
    def _execute(c):
        threshold_val = float(min_attendance_threshold) if min_attendance_threshold is not None else float(
            get_setting("min_attendance_threshold", 75, conn=c)
        )
        all_students = identify_weak_students(department_id, threshold_val, conn=c)
        weak_students = [s for s in all_students if s["is_weak"]]

        dept_clause = "WHERE department_id = ?" if department_id else ""
        params = [department_id] if department_id else []

        total_batches = c.execute(
            f"SELECT COUNT(*) AS c FROM remedial_batches {dept_clause}", params
        ).fetchone()["c"]

        scheduled_batches = c.execute(
            f"SELECT COUNT(*) AS c FROM remedial_batches {dept_clause} {'AND' if department_id else 'WHERE'} status IN ('SCHEDULED', 'IN_PROGRESS')",
            params
        ).fetchone()["c"]

        dept_r_clause = "WHERE r.department_id = ?" if department_id else "WHERE 1=1"
        difficult = c.execute(
            f"""
            SELECT s.name, s.code, COUNT(*) AS fail_count
            FROM results r
            JOIN subjects s ON s.id = r.subject_id
            {dept_r_clause} AND r.result = 'FAIL'
            GROUP BY s.id
            ORDER BY fail_count DESC
            LIMIT 6
            """,
            params,
        ).fetchall()

        return {
            "total_students_analyzed": len(all_students),
            "all_students": all_students,
            "weak_students": weak_students,
            "total_remedial_required": len(weak_students),
            "total_backlog_students": sum(1 for s in weak_students if s["has_backlogs"]),
            "total_low_attendance_students": sum(1 for s in weak_students if s["has_low_attendance"]),
            "total_both_students": sum(1 for s in weak_students if s["reason_for_selection"] == "Both"),
            "total_batches": total_batches,
            "scheduled_batches": scheduled_batches,
            "threshold": threshold_val,
            "difficult_subjects": [dict(r) for r in difficult],
        }

    if conn is not None:
        return _execute(conn)
    with get_db() as c:
        return _execute(c)


def identify_topper_for_batch(batch_id, conn):
    """Find the best-performing student who could mentor a given remedial batch.
    Topper = same semester/section/batch, zero failures, highest CGPA prioritized."""
    batch = conn.execute(
        "SELECT * FROM remedial_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if not batch:
        return None

    row = conn.execute("""
        SELECT s.id, s.student_id, s.full_name, s.cgpa,
               AVG(r.marks) AS avg_marks,
               COUNT(CASE WHEN r.result = 'FAIL' THEN 1 END) AS fail_count
        FROM students s
        LEFT JOIN results r ON UPPER(r.student_id) = UPPER(s.student_id)
        WHERE s.semester = ?
          AND s.section = ?
          AND s.department_id = ?
          AND (s.academic_batch = ? OR (s.academic_batch IS NULL AND ? IS NULL))
        GROUP BY s.id
        HAVING fail_count = 0
         ORDER BY s.cgpa DESC, avg_marks DESC, s.student_id ASC
        LIMIT 1
    """, (
        batch["semester"], batch["section"], batch["department_id"],
        batch["academic_batch"], batch["academic_batch"],
    )).fetchone()
    return dict(row) if row else None


def auto_assign_peer_mentors(department_id=None, conn=None):
    """Auto-assign toppers as peer mentors for unmentored active remedial batches."""
    def _execute(c):
        query = """
            SELECT rb.id
            FROM remedial_batches rb
            LEFT JOIN peer_mentors pm ON pm.batch_id = rb.id
            WHERE pm.id IS NULL
              AND rb.status IN ('SCHEDULED', 'IN_PROGRESS')
        """
        params = []
        if department_id:
            query += " AND rb.department_id = ?"
            params.append(department_id)
        batches = c.execute(query, params).fetchall()

        assigned = 0
        for batch in batches:
            topper = identify_topper_for_batch(batch["id"], c)
            if topper:
                c.execute("""
                    INSERT OR IGNORE INTO peer_mentors
                    (mentor_student_id, batch_id, assigned_by)
                    VALUES (?, ?, 'AUTO')
                """, (topper["id"], batch["id"]))
                assigned += 1
        return assigned

    if conn is not None:
        return _execute(conn)
    with get_db() as c:
        res = _execute(c)
        c.commit()
        return res
