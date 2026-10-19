import csv
import io
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

import ai_engine
from academic import (
    ACADEMIC_BATCHES,
    COMPUTER_DEPT_CODE,
    LOW_ATTENDANCE_PERCENT,
    SEMESTERS,
    academic_batch_from_enrollment,
    is_computer_department,
    scope_upload_records,
)
from auth import (
    get_current_user,
    hash_password,
    login_required,
    login_status,
    login_user,
    record_failed_login,
    get_failed_logins,
    generate_reset_token,
    validate_reset_token,
    mark_token_used,
    cleanup_expired_tokens,
    send_email,
)
from db import add_notification, get_db, get_setting, log_audit, set_setting
from excel_parser import (
    exclude_year_back_students,
    parse_attc_backlog_file,
    parse_attendance_file,
    parse_backlog_file,
    parse_result_file,
)

def _exclude_year_back_placeholder(records, excluded_ids=()):
    """Placeholder function - year-back exclusion removed."""
    return records, []

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

auth_bp = Blueprint("auth", __name__)
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
teacher_bp = Blueprint("teacher", __name__, url_prefix="/teacher")
student_bp = Blueprint("student", __name__, url_prefix="/student")


def _computer_dept_id(conn):
    row = conn.execute(
        "SELECT id FROM departments WHERE code = ?", (COMPUTER_DEPT_CODE,)
    ).fetchone()
    return row["id"] if row else None


def _dept_scope(user, conn=None):
    """Return department scope; super admins intentionally have global scope."""
    closer = None
    if conn is None:
        closer = get_db()
        conn = closer
    try:
        co_id = _computer_dept_id(conn)
        if user["role"] == "super_admin":
            return None
        if user.get("dept_code") and not is_computer_department(user["dept_code"]):
            return co_id
        return user["department_id"] or co_id
    finally:
        if closer is not None:
            closer.close()


def _is_co_admin(user):
    return user and user["role"] == "dept_admin" and is_computer_department(user.get("dept_code"))


def _departments(conn):
    return conn.execute("SELECT id, code, name FROM departments ORDER BY name").fetchall()


def _notify_dept_admins(conn, department_id, message):
    rows = conn.execute(
        """
        SELECT id FROM users
        WHERE role = 'dept_admin' AND department_id = ? AND is_active = 1
        """,
        (department_id,),
    ).fetchall()
    for row in rows:
        add_notification(row["id"], message, conn=conn)


def _ensure_student_login(conn, student_id, full_name, department_id):
    """Create or repair the deterministic student account and link it to the profile."""
    username = str(student_id).strip()
    user = conn.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
    if not user:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, full_name, department_id) VALUES (?, ?, 'student', ?, ?)",
            (username, hash_password("student123"), full_name, department_id),
        )
        user_id = cur.lastrowid
    else:
        user_id = user["id"]
        conn.execute(
            "UPDATE users SET full_name = ?, department_id = ?, role = 'student' WHERE id = ?",
            (full_name, department_id, user_id),
        )
    return user_id


def _is_year_back_record(rec):
    """Return True when a result row is explicitly marked as year-back."""
    value = rec.get("year_back_status", rec.get("year_back"))
    text = str(value or "").strip().upper().replace("_", " ")
    return text in {"YES", "Y", "TRUE", "1", "YEAR BACK", "YEAR-BACK", "YEARBACK"}


def _student_is_year_back(conn, student_pk):
    return bool(conn.execute(
        "SELECT 1 FROM student_backlog_subjects WHERE student_id = ? AND year_back_status = 'YES' LIMIT 1",
        (student_pk,),
    ).fetchone())


def _import_result_records(conn, records, dept_id_filter=None):
    """Insert parsed result records into the database with deduplication."""
    inserted = 0
    skipped = []

    records, year_back_warnings = _exclude_year_back_placeholder(records)
    skipped.extend(year_back_warnings)
    for idx, rec in enumerate(records, start=1):
        if _is_year_back_record(rec):
            skipped.append(f"Record {idx}: skipped year-back student {rec.get('student_id', 'unknown')} from result upload")
            continue
        dept = conn.execute(
            "SELECT id, code FROM departments WHERE code = ? OR name = ?",
            (rec["department"], rec["department"]),
        ).fetchone()
        if not dept:
            skipped.append(f"Record {idx}: unknown department {rec['department']}")
            continue
        if dept["code"] != COMPUTER_DEPT_CODE:
            skipped.append(f"Record {idx}: skipped non-Computer Engineering ({rec['student_id']})")
            continue
        if dept_id_filter and dept["id"] != dept_id_filter:
            skipped.append(f"Record {idx}: outside Computer Engineering ({rec['student_id']})")
            continue
        academic_batch = rec.get("academic_batch") or ""

        subject_name = rec["subject"]
        subject_code = rec.get("subject_code") or subject_name[:12].upper().replace(" ", "")
        
        # Use exact matching only - no partial matches to prevent theory/lab confusion
        subject = conn.execute(
            "SELECT id FROM subjects WHERE UPPER(name) = UPPER(?)",
            (subject_name,),
        ).fetchone()
        
        # If no exact name match, try exact code match
        if not subject:
            subject = conn.execute(
                "SELECT id FROM subjects WHERE UPPER(code) = UPPER(?)",
                (subject_code,),
            ).fetchone()
        
        if not subject:
            cur = conn.execute(
                "INSERT INTO subjects (code, name, department_id) VALUES (?, ?, ?)",
                (subject_code, subject_name, dept["id"]),
            )
            subject_id = cur.lastrowid
        else:
            subject_id = subject["id"]

        sid = rec["student_id"]
        student = conn.execute(
            "SELECT id FROM students WHERE student_id = ?", (sid,)
        ).fetchone()
        if student and _student_is_year_back(conn, student["id"]):
            skipped.append(f"Record {idx}: skipped year-back student {sid} from result upload")
            continue
        if not student:
            cur = conn.execute(
                """
                INSERT INTO students
                (student_id, full_name, department_id, semester, section, academic_batch)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sid, rec["student_name"], dept["id"], int(rec["semester"]), rec["section"], academic_batch),
            )
            student_pk = cur.lastrowid
        else:
            student_pk = student["id"]
            conn.execute(
                """
                UPDATE students
                SET full_name = ?, department_id = ?, semester = ?, section = ?, academic_batch = ?
                WHERE id = ?
                """,
                (rec["student_name"], dept["id"], int(rec["semester"]), rec["section"], academic_batch, student_pk),
            )
        conn.execute("UPDATE students SET user_id = ? WHERE id = ?", (
            _ensure_student_login(conn, sid, rec["student_name"], dept["id"]), student_pk
        ))

        # Upsert results to prevent duplicate failures on re-upload
        existing_result = conn.execute(
            """
            SELECT id FROM results
            WHERE student_id = ? AND semester = ? AND subject_id = ?
            """,
            (sid, int(rec["semester"]), subject_id),
        ).fetchone()

        if existing_result:
            conn.execute(
                """
                UPDATE results
                SET marks = ?, pass_marks = ?, result = ?, student_name = ?,
                    section = ?, department_id = ?, academic_batch = ?,
                    uploaded_at = datetime('now')
                WHERE id = ?
                """,
                (
                    rec["marks"], rec["pass_marks"], rec["result"],
                    rec["student_name"], rec["section"], dept["id"], academic_batch,
                    existing_result["id"],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO results
                (student_id, student_name, department_id, semester, section,
                 academic_batch, subject_id, marks, pass_marks, result)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid, rec["student_name"], dept["id"], int(rec["semester"]),
                    rec["section"], academic_batch, subject_id, rec["marks"], rec["pass_marks"], rec["result"],
                ),
            )
        inserted += 1

    return inserted, skipped


def _get_or_create_subject(conn, name, code, department_id):
    """
    Get or create a subject using EXACT matching only.
    Prevents incorrect matches between theory and lab subjects.
    Example: "Data structure in C" should NOT match "Data structure lab"
    """
    name = (name or "").strip() or "Overall"
    code = (code or "").strip() or name[:12].upper().replace(" ", "")
    
    # 1. Try exact name match (case-insensitive) - this is the primary check
    subject = conn.execute(
        "SELECT id FROM subjects WHERE UPPER(name) = UPPER(?)",
        (name,),
    ).fetchone()
    if subject:
        return subject["id"]
    
    # 2. Try exact code match (case-insensitive) - secondary check
    subject = conn.execute(
        "SELECT id FROM subjects WHERE UPPER(code) = UPPER(?)",
        (code,),
    ).fetchone()
    if subject:
        return subject["id"]
    
    # 3. No exact match found - create new subject
    # We do NOT use partial/LIKE matching to prevent incorrect matches
    cur = conn.execute(
        "INSERT INTO subjects (code, name, department_id) VALUES (?, ?, ?)",
        (code, name, department_id),
    )
    return cur.lastrowid


# ---------------------------------------------------------
# AUTH & NOTIFICATIONS
# ---------------------------------------------------------

@auth_bp.route("/")
def index():
    if session.get("user_id"):
        user = get_current_user()
        if not user:
            session.clear()
            return redirect(url_for("auth.login"))
        if user["role"] in ("super_admin", "dept_admin"):
            return redirect(url_for("admin.dashboard"))
        if user["role"] == "teacher":
            return redirect(url_for("teacher.dashboard"))
        return redirect(url_for("student.dashboard"))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user, status = login_status(username, password)
        if status == "pending":
            flash("Your registration is waiting for Computer Engineering admin approval.", "warning")
            return render_template("login.html")
        if status == "rejected":
            flash("Your registration was rejected. Please contact the department admin.", "danger")
            return render_template("login.html")
        if status != "ok" or not user:
            record_failed_login(username, request.remote_addr, request.headers.get("User-Agent"))
            flash("Invalid username or password.", "danger")
            return render_template("login.html")
        session["user_id"] = user["id"]
        session["role"] = user["role"]
        session.permanent = True  # Make session persistent with configured lifetime
        log_audit(user["id"], "LOGIN", f"User {username} logged in")
        return redirect(url_for("auth.index"))
    return render_template("login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("auth.index"))

    form = {
        "roll_number": "",
        "full_name": "",
        "email": "",
        "semester": "1",
        "section": "A",
        "department_id": "",
    }
    if request.method != "POST":
        with get_db() as conn:
            departments = conn.execute("SELECT id, code, name FROM departments ORDER BY name").fetchall()
        return render_template("register.html", form=form, semesters=SEMESTERS, departments=departments)

    roll = request.form.get("roll_number", "").strip().upper()
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", "")
    section = (request.form.get("section", "A") or "A").strip().upper()[:5] or "A"
    department_id = request.form.get("department_id", type=int)
    form.update({"roll_number": roll, "full_name": full_name, "email": email, "section": section, "department_id": department_id or ""})

    try:
        semester = int(request.form.get("semester", 1))
    except (TypeError, ValueError):
        semester = 1
    form["semester"] = str(semester)

    if not roll or not full_name:
        flash("Roll number and full name are required.", "danger")
        with get_db() as conn:
            departments = conn.execute("SELECT id, code, name FROM departments ORDER BY name").fetchall()
        return render_template("register.html", form=form, semesters=SEMESTERS, departments=departments)
    if not email:
        flash("Email address is required.", "danger")
        with get_db() as conn:
            departments = conn.execute("SELECT id, code, name FROM departments ORDER BY name").fetchall()
        return render_template("register.html", form=form, semesters=SEMESTERS, departments=departments)
    if "@" not in email or "." not in email:
        flash("Please enter a valid email address.", "danger")
        with get_db() as conn:
            departments = conn.execute("SELECT id, code, name FROM departments ORDER BY name").fetchall()
        return render_template("register.html", form=form, semesters=SEMESTERS, departments=departments)
    if len(password) < 4:
        flash("Password must be at least 4 characters.", "danger")
        with get_db() as conn:
            departments = conn.execute("SELECT id, code, name FROM departments ORDER BY name").fetchall()
        return render_template("register.html", form=form, semesters=SEMESTERS, departments=departments)
    if password != confirm:
        flash("Passwords do not match.", "danger")
        with get_db() as conn:
            departments = conn.execute("SELECT id, code, name FROM departments ORDER BY name").fetchall()
        return render_template("register.html", form=form, semesters=SEMESTERS, departments=departments)
    if semester not in SEMESTERS:
        flash("Please choose a valid semester.", "danger")
        with get_db() as conn:
            departments = conn.execute("SELECT id, code, name FROM departments ORDER BY name").fetchall()
        return render_template("register.html", form=form, semesters=SEMESTERS, departments=departments)
    if not department_id:
        flash("Please select a department.", "danger")
        with get_db() as conn:
            departments = conn.execute("SELECT id, code, name FROM departments ORDER BY name").fetchall()
        return render_template("register.html", form=form, semesters=SEMESTERS, departments=departments)

    reserved = {
        "superadmin", "coadmin", "meadmin", "mtadmin", "ceadmin", "mcadmin",
        "cot01", "cot02", "met01", "mtt01", "cet01", "mct01",
    }
    if roll.lower() in reserved:
        flash("That roll number cannot be used for student registration.", "danger")
        with get_db() as conn:
            departments = conn.execute("SELECT id, code, name FROM departments ORDER BY name").fetchall()
        return render_template("register.html", form=form, semesters=SEMESTERS, departments=departments)

    with get_db() as conn:
        dept = conn.execute("SELECT id FROM departments WHERE id = ?", (department_id,)).fetchone()
        if not dept:
            flash("Selected department is not valid.", "danger")
            return render_template("register.html", form=form, semesters=SEMESTERS, departments=departments)

        taken_user = conn.execute(
            "SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (roll,)
        ).fetchone()
        taken_student = conn.execute(
            "SELECT id, user_id FROM students WHERE UPPER(student_id) = UPPER(?)", (roll,)
        ).fetchone()
        
        if taken_user:
            flash("This roll number is already registered. Please log in.", "warning")
            return redirect(url_for("auth.login"))
        
        # If student exists in students table but not in users (old data), clean it up
        if taken_student and not taken_student["user_id"]:
            conn.execute(
                "DELETE FROM students WHERE student_id = ?",
                (roll,)
            )

        # Create user
        cur = conn.execute(
            """
            INSERT INTO users (username, password_hash, role, full_name, department_id)
            VALUES (?, ?, 'student', ?, ?)
            """,
            (roll, hash_password(password), full_name, dept["id"]),
        )
        user_id = cur.lastrowid
        academic_batch = academic_batch_from_enrollment(roll, "2024-2027")
        
        # Create student record
        conn.execute(
            """
            INSERT INTO students
            (student_id, full_name, department_id, semester, section, academic_batch, user_id, email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (roll, full_name, dept["id"], semester, section, academic_batch, user_id, email),
        )
        conn.commit()

    flash("Registration successful. You can now log in with your roll number.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/logout")
def logout():
    user = get_current_user()
    if user:
        log_audit(user["id"], "LOGOUT", f"User {user['username']} logged out")
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/notifications")
@login_required()
def notifications():
    user = get_current_user()
    with get_db() as conn:
        items = conn.execute(
            """
            SELECT * FROM notifications
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (user["id"],),
        ).fetchall()
    return render_template("notifications.html", user=user, notifications=items)


@auth_bp.route("/notifications/read/<int:id>", methods=["POST"])
@login_required()
def mark_notification_read(id):
    user = get_current_user()
    with get_db() as conn:
        conn.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
            (id, user["id"]),
        )
        conn.commit()
    return redirect(url_for("auth.notifications"))


@auth_bp.route("/notifications/read-all", methods=["POST"])
@login_required()
def mark_all_notifications_read():
    user = get_current_user()
    with get_db() as conn:
        conn.execute(
            "UPDATE notifications SET is_read = 1 WHERE user_id = ?",
            (user["id"],),
        )
        conn.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("auth.notifications"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Handle password reset requests."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if not username:
            flash("Please enter your username, employee ID, or roll number.", "warning")
            return render_template("forgot_password.html")
        
        # Clean up expired tokens first
        cleanup_expired_tokens()
        
        with get_db() as conn:
            # Try to find user by username, student_id, or employee_id and get email
            user = conn.execute(
                """
                SELECT u.*, s.student_id, s.email AS student_email, t.employee_id, t.email AS teacher_email
                FROM users u
                LEFT JOIN students s ON s.user_id = u.id
                LEFT JOIN teachers t ON t.user_id = u.id
                WHERE LOWER(u.username) = LOWER(?)
                   OR UPPER(s.student_id) = UPPER(?)
                   OR UPPER(t.employee_id) = UPPER(?)
                """,
                (username, username, username),
            ).fetchone()
            
            if not user:
                # Don't reveal if user exists for security
                flash("If an account exists with that identifier, a password reset link will be sent to the registered email address.", "info")
                return render_template("forgot_password.html")
            
            # Get email based on user role:
            # - student: from students table
            # - teacher: from teachers table
            # - dept_admin, super_admin: from users table
            user_email = None
            role = user["role"]
            if role == "student":
                user_email = user["student_email"]
            elif role == "teacher":
                user_email = user["teacher_email"]
            elif role in ("dept_admin", "super_admin"):
                user_email = user["email"]
            
            if not user_email:
                flash("This account does not have a registered email address. Please contact your department administrator.", "warning")
                return render_template("forgot_password.html")
            
            # Generate reset token (expires in 15 minutes)
            token = generate_reset_token(user["id"], expiry_minutes=15)
            
            # Generate reset URL
            # Use localhost for local testing, will need to update for production
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            
            # Send email
            subject = "Password Reset Request - RemedialPro"
            body = f"""
Hello {user['full_name']},

You have requested a password reset for your RemedialPro account.

Click the link below to reset your password (this link will expire in 15 minutes):

{reset_url}

If you did not request this password reset, please ignore this email and your password will remain unchanged.

For security reasons, please do not share this link with anyone.

Best regards,
RemedialPro Team
"""
            
            html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #0f172a; color: white; padding: 20px; text-align: center; }}
        .content {{ background-color: #f8fafc; padding: 20px; border-radius: 5px; }}
        .button {{ display: inline-block; background-color: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; margin: 20px 0; }}
        .footer {{ margin-top: 20px; text-align: center; color: #64748b; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>RemedialPro</h2>
        </div>
        <div class="content">
            <p>Hello {user['full_name']},</p>
            <p>You have requested a password reset for your RemedialPro account.</p>
            <p>Click the button below to reset your password (this link will expire in 15 minutes):</p>
            <center>
                <a href="{reset_url}" class="button">Reset Password</a>
            </center>
            <p>Or copy and paste this link into your browser:</p>
            <p style="word-break: break-all; color: #2563eb;">{reset_url}</p>
            <p>If you did not request this password reset, please ignore this email and your password will remain unchanged.</p>
            <p>For security reasons, please do not share this link with anyone.</p>
        </div>
        <div class="footer">
            <p>Best regards,<br>RemedialPro Team</p>
        </div>
    </div>
</body>
</html>
"""
            
            email_sent = send_email(user_email, subject, body, html_body)
            
            if email_sent:
                flash("A password reset link has been sent to your registered email address. Please check your inbox (and spam folder).", "success")
            else:
                flash("There was an error sending the email. Please try again or contact your administrator.", "danger")
            
            return redirect(url_for("auth.login"))
    
    return render_template("forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Handle password reset with token."""
    # Clean up expired tokens
    cleanup_expired_tokens()
    
    user_id = validate_reset_token(token)
    if not user_id:
        flash("Invalid or expired reset link. Please request a new password reset.", "danger")
        return redirect(url_for("auth.forgot_password"))
    
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        
        if len(password) < 4:
            flash("Password must be at least 4 characters.", "danger")
            return render_template("reset_password.html", token=token)
        
        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("reset_password.html", token=token)
        
        with get_db() as conn:
            # Update password
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(password), user_id),
            )
            # Mark token as used (pass connection to avoid nested connection lock)
            mark_token_used(token, conn)
            conn.commit()
        
        flash("Password has been reset successfully. You can now log in with your new password.", "success")
        return redirect(url_for("auth.login"))
    
    return render_template("reset_password.html", token=token)


# ---------------------------------------------------------
# ADMIN PORTAL
# ---------------------------------------------------------

@admin_bp.route("/dashboard")
@login_required(roles=["super_admin", "dept_admin"])
def dashboard():
    user = get_current_user()
    dept_id = _dept_scope(user)
    analytics = ai_engine.get_analytics(dept_id)
    with get_db() as conn:
        params = [dept_id] if dept_id else []
        dept_clause = "WHERE department_id = ?" if dept_id else ""
        batch_count = conn.execute(
            f"SELECT COUNT(*) AS c FROM remedial_batches {dept_clause}", params
        ).fetchone()["c"]
        student_count = conn.execute(
            f"SELECT COUNT(*) AS c FROM students {dept_clause}", params
        ).fetchone()["c"]
        scheduled_count = conn.execute(
            f"SELECT COUNT(*) AS c FROM timetable tt JOIN remedial_batches rb ON rb.id = tt.batch_id {dept_clause.replace('department_id', 'rb.department_id')}",
            params,
        ).fetchone()["c"]
        totals_query = """
            SELECT COALESCE(r.academic_batch, 'Unknown') AS academic_batch,
                   r.semester,
                   COUNT(*) AS fail_count,
                   COUNT(DISTINCT r.student_id) AS student_count
            FROM results r
            WHERE r.result = 'FAIL'
        """
        totals_params = []
        if dept_id:
            totals_query += " AND r.department_id = ?"
            totals_params.append(dept_id)
        totals_query += " GROUP BY r.academic_batch, r.semester ORDER BY r.academic_batch, r.semester"
        batch_totals = conn.execute(totals_query, totals_params).fetchall()

        # Department-wise statistics for super admin
        department_stats = None
        if user["role"] == "super_admin":
            department_stats = conn.execute("""
                SELECT d.id, d.code, d.name,
                       COUNT(DISTINCT st.id) AS student_count,
                       COUNT(DISTINCT rb.id) AS batch_count,
                       COUNT(DISTINCT tt.id) AS scheduled_count,
                       COALESCE(AVG(sa.attendance_percent), 0) AS avg_attendance,
                       (SELECT COUNT(*) FROM results r WHERE r.department_id = d.id AND r.result = 'FAIL') AS failure_count
                FROM departments d
                LEFT JOIN students st ON st.department_id = d.id
                LEFT JOIN remedial_batches rb ON rb.department_id = d.id
                LEFT JOIN timetable tt ON tt.batch_id = rb.id
                LEFT JOIN semester_attendance sa ON sa.student_id = st.id
                GROUP BY d.id, d.code, d.name
                ORDER BY d.name
            """).fetchall()

    return render_template(
        "admin/dashboard.html",
        user=user,
        analytics=analytics,
        batch_count=batch_count,
        student_count=student_count,
        scheduled_count=scheduled_count,
        batch_totals=batch_totals,
        academic_batches=ACADEMIC_BATCHES,
        semesters=SEMESTERS,
        department_stats=department_stats,
    )


@admin_bp.route("/upload-results", methods=["GET", "POST"])
@login_required(roles=["super_admin", "dept_admin"])
def upload_results():
    """Compatibility route for the renamed Upload Result page."""
    flash("Use Upload Result to import result sheets and review academic support needs.", "info")
    return redirect(url_for("admin.attc_backlog_analysis"))


@admin_bp.route("/upload-backlogs", methods=["GET", "POST"])
@login_required(roles=["super_admin", "dept_admin"])
def upload_backlogs():
    """Unified into ATTC Backlog Analysis."""
    flash("Backlog records are reviewed from the Upload Result page.", "info")
    return redirect(url_for("admin.attc_backlog_analysis"))


@admin_bp.route("/upload-attc-backlog", methods=["GET", "POST"])
@admin_bp.route("/attc-backlog-analysis", methods=["GET", "POST"])
@login_required(roles=["super_admin", "dept_admin"])
def attc_backlog_analysis():
    """
    Central Upload Result & Remedial Allocation Dashboard:
    - Automatically identifies student failures and low-attendance students.
    - Displays Weak Student List with all 9 required metrics.
    - Handles unified uploads (ATTC format or standard result format).
    - Allocates remedial classes and generates schedules.
    """
    user = get_current_user()
    dept_id = _dept_scope(user)
    preview_data = None

    if request.method == "POST":
        # A failed or invalid upload must never leave an older preview confirmable.
        session.pop("attc_parsed", None)
        session.pop("attc_file_path", None)
        session.pop("is_attc", None)
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Please choose an Excel (.xlsx, .xls, .xlsm) or CSV file to upload.", "warning")
            return redirect(url_for("admin.attc_backlog_analysis"))

        filename = secure_filename(file.filename)
        ext = filename.rsplit(".", 1)[-1].lower()
        if ext not in {"xlsx", "xls", "xlsm", "csv"}:
            flash("Only .xlsx, .xls, .xlsm and .csv files are supported.", "danger")
            return redirect(url_for("admin.attc_backlog_analysis"))

        path = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
        file.save(path)

        parsed = None
        is_attc = False

        # Attempt parsing as ATTC Backlog Analysis format first
        if ext in {"xlsx", "xls", "xlsm"}:
            try:
                academic_batch = (request.form.get("academic_batch") or "").strip()
                parsed = parse_attc_backlog_file(path, ext, academic_batch_override=academic_batch or None)
                is_attc = True
            except Exception:
                parsed = None

        # Fallback to standard result parser if not ATTC format
        if not parsed:
            try:
                academic_batch = (request.form.get("academic_batch") or "").strip()
                parsed = parse_result_file(
                    path,
                    ext,
                    default_semester=None,
                    department_override=COMPUTER_DEPT_CODE,
                    academic_batch_override=academic_batch or None,
                )
                is_attc = False
            except Exception as exc:
                flash(f"Could not parse file: {exc}", "danger")
                return redirect(url_for("admin.attc_backlog_analysis"))

        if is_attc:
            parsed["records"], year_back_warnings = _exclude_year_back_placeholder(parsed.get("records", []))
            parsed["skipped"] = year_back_warnings + parsed.get("skipped", [])
            parsed["students_found"] = len({r.get("student_id", "").strip().upper() for r in parsed["records"]})
            parsed["total_backlog_subjects"] = sum(
                len(subjects.get("failed_subjects", [])) or int(subjects.get("backlog_count", 0) or 0)
                for record in parsed["records"] for subjects in record.get("semester_backlogs", [])
            )
        else:
            parsed["records"], year_back_warnings = _exclude_year_back_placeholder(parsed.get("records", []))
            parsed["skipped"] = year_back_warnings + parsed.get("skipped", [])
            parsed["students_found"] = len({r.get("student_id", "").strip().upper() for r in parsed["records"]})

        if not parsed.get("records"):
            flash("Upload contains no eligible students after year-back exclusion.", "warning")
            return redirect(url_for("admin.attc_backlog_analysis"))

        if is_attc:
            preview_data = {
                "format": "ATTC Backlog Analysis (22 Columns)",
                "students_found": parsed["students_found"],
                "total_backlog_subjects": parsed["total_backlog_subjects"],
                "skipped": parsed.get("skipped", [])[:10],
                "sample_records": parsed["records"][:5],
            }
            session["attc_parsed"] = parsed
            session["attc_file_path"] = str(path)
            session["is_attc"] = True
            flash(
                f"Preview: Found {parsed['students_found']} students with {parsed['total_backlog_subjects']} backlog entries in ATTC format. Review and confirm import.",
                "info",
            )
        else:
            scoped_records = parsed.get("records", [])
            preview_data = {
                "format": "Standard College Result / Backlog Sheet",
                "students_found": parsed.get("students_found", len(scoped_records)),
                "total_backlog_subjects": len([r for r in scoped_records if r.get("result") == "FAIL"]),
                "skipped": parsed.get("skipped", [])[:10],
                "sample_records": scoped_records[:5],
            }
            session["attc_parsed"] = parsed
            session["attc_file_path"] = str(path)
            session["is_attc"] = False
            flash(
                f"Preview: Found {preview_data['students_found']} student records with {preview_data['total_backlog_subjects']} failure(s). Review and confirm import.",
                "info",
            )

    # Load analysis from database
    threshold_param = request.args.get("threshold")
    with get_db() as conn:
        current_threshold = float(threshold_param) if threshold_param else float(
            get_setting("min_attendance_threshold", 75.0, conn=conn)
        )
        analysis = ai_engine.get_attc_backlog_analysis(
            dept_id, min_attendance_threshold=current_threshold, conn=conn
        )
        weak_students = analysis["weak_students"]

        # Filter weak students by user parameters
        q = request.args.get("q", "").strip().lower()
        filter_batch = request.args.get("academic_batch", "").strip()
        filter_reason = request.args.get("reason", "").strip()

        filtered_weak = []
        for s in weak_students:
            if q:
                if q not in str(s["student_name"]).lower() and q not in str(s["roll_number"]).lower():
                    continue
            if filter_batch:
                if (s.get("academic_batch") or "") != filter_batch:
                    continue
            if filter_reason and filter_reason != "All":
                if filter_reason == "Low Attendance" and s["reason_for_selection"] != "Low Attendance":
                    continue
                elif filter_reason == "Backlog" and s["reason_for_selection"] != "Backlog":
                    continue
                elif filter_reason == "Both" and s["reason_for_selection"] != "Both":
                    continue
            filtered_weak.append(s)

        batch_query = "SELECT DISTINCT academic_batch FROM students WHERE academic_batch IS NOT NULL AND academic_batch != ''"
        batch_params = []
        if dept_id:
            batch_query += " AND department_id = ?"
            batch_params.append(dept_id)
        batch_query += " ORDER BY academic_batch"
        stored_batches = [r["academic_batch"] for r in conn.execute(batch_query, batch_params).fetchall()]
        academic_batches = list(dict.fromkeys(ACADEMIC_BATCHES + stored_batches))

    return render_template(
        "admin/attc_backlog_analysis.html",
        user=user,
        analysis=analysis,
        weak_students=filtered_weak,
        total_weak_filtered=len(filtered_weak),
        threshold=current_threshold,
        preview_data=preview_data,
        q=q,
        filter_sem="",
        filter_batch=filter_batch,
        filter_reason=filter_reason,
        academic_batches=academic_batches,
        semesters=SEMESTERS,
    )


@admin_bp.route("/confirm-attc-import", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def confirm_attc_import():
    """Confirm and import parsed ATTC or result data into database."""
    user = get_current_user()
    dept_id = _dept_scope(user)

    parsed = session.get("attc_parsed")
    file_path = session.get("attc_file_path")
    is_attc = session.get("is_attc", True)

    if not parsed or not file_path:
        flash("Session expired. Please upload the file again.", "warning")
        return redirect(url_for("admin.attc_backlog_analysis"))

    # Re-apply identity-level exclusion at confirmation in case the preview was
    # created by an older parser/session or was tampered with client-side.
    if is_attc:
        parsed["records"], _ = _exclude_year_back_placeholder(parsed.get("records", []))
    else:
        parsed["records"], _ = _exclude_year_back_placeholder(parsed.get("records", []))
    if not parsed.get("records"):
        session.pop("attc_parsed", None)
        session.pop("attc_file_path", None)
        session.pop("is_attc", None)
        flash("Upload contains no eligible students after year-back exclusion.", "warning")
        return redirect(url_for("admin.attc_backlog_analysis"))

    auto_schedule = request.form.get("auto_schedule") == "1"

    with get_db() as conn:
        inserted_students = 0
        inserted_backlogs = 0
        updated_cgpa = 0

        # Ensure we have a valid department_id for student insertion
        # _dept_scope returns None for super_admin, but students table requires NOT NULL department_id
        # ATTC format is for Computer Engineering, so fall back to computer department ID
        co_dept_id = _computer_dept_id(conn)
        effective_dept_id = dept_id or co_dept_id

        if is_attc:
            for student_rec in parsed["records"]:
                enroll_id = student_rec["student_id"]
                name = student_rec["student_name"]
                cgpa = student_rec.get("cgpa")
                current_semester = int(student_rec.get("current_semester") or 0)
                academic_batch = student_rec.get("academic_batch") or academic_batch_from_enrollment(enroll_id)

                student = conn.execute(
                    "SELECT id, cgpa FROM students WHERE student_id = ?", (enroll_id,)
                ).fetchone()

                if student:
                    student_pk = student["id"]
                    if cgpa is not None and cgpa > 0:
                        if student["cgpa"] is None or student["cgpa"] != cgpa:
                            conn.execute(
                                "UPDATE students SET cgpa = ? WHERE id = ?",
                                (cgpa, student_pk),
                            )
                            updated_cgpa += 1
                    if current_semester > 0:
                        conn.execute(
                            "UPDATE students SET semester = ? WHERE id = ?",
                            (current_semester, student_pk),
                        )
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO students
                        (student_id, full_name, department_id, semester, section, academic_batch, cgpa)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (enroll_id, name, effective_dept_id, current_semester, "A", academic_batch, cgpa),
                    )
                    student_pk = cur.lastrowid
                    inserted_students += 1

                conn.execute("UPDATE students SET user_id = ? WHERE id = ?", (
                    _ensure_student_login(conn, enroll_id, name, effective_dept_id), student_pk
                ))

                for sem_data in student_rec["semester_backlogs"]:
                    semester = sem_data["semester"]
                    failed_subjects = sem_data["failed_subjects"]
                    needs_verification = sem_data["needs_verification"]

                    if not failed_subjects and sem_data.get("backlog_count", 0):
                        failed_subjects = [
                            f"Unspecified backlog (Sem-{semester}) #{n + 1}"
                            for n in range(int(sem_data["backlog_count"]))
                        ]

                    for subject_name in failed_subjects:
                        subject_code = subject_name[:12].upper().replace(" ", "")
                        
                        # Use exact matching only - no partial matches to prevent theory/lab confusion
                        subject = conn.execute(
                            "SELECT id FROM subjects WHERE UPPER(name) = UPPER(?)",
                            (subject_name,),
                        ).fetchone()
                        
                        # If no exact name match, try exact code match
                        if not subject:
                            subject = conn.execute(
                                "SELECT id FROM subjects WHERE UPPER(code) = UPPER(?)",
                                (subject_code,),
                            ).fetchone()
                        
                        if not subject:
                            cur = conn.execute(
                                "INSERT INTO subjects (code, name, department_id) VALUES (?, ?, ?)",
                                (subject_code, subject_name, effective_dept_id),
                            )
                            subject_id = cur.lastrowid
                        else:
                            subject_id = subject["id"]

                        existing = conn.execute(
                            """
                            SELECT id, status FROM student_backlog_subjects
                            WHERE student_id = ? AND subject_id = ? AND original_semester = ?
                            """,
                            (student_pk, subject_id, semester),
                        ).fetchone()

                        status = "NEEDS_VERIFICATION" if needs_verification else "PENDING"
                        if existing:
                            if existing["status"] != "CLEARED":
                                conn.execute(
                                    """
                                    UPDATE student_backlog_subjects
                                    SET status = ?, updated_at = datetime('now')
                                    WHERE id = ?
                                    """,
                                    (status, existing["id"]),
                                )
                                inserted_backlogs += 1
                        else:
                            conn.execute(
                                """
                                INSERT INTO student_backlog_subjects
                                (student_id, subject_id, original_semester, status)
                                VALUES (?, ?, ?, ?)
                                """,
                                (student_pk, subject_id, semester, status),
                            )
                            inserted_backlogs += 1

                        conn.execute(
                            """
                            INSERT OR IGNORE INTO results
                            (student_id, student_name, department_id, semester, section,
                             academic_batch, subject_id, marks, pass_marks, result)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (enroll_id, name, effective_dept_id, semester, "A",
                              academic_batch, subject_id, 0, 40, "FAIL"),
                        )
        else:
            inserted_students, import_skipped = _import_result_records(conn, parsed.get("records", []), dept_id_filter=dept_id)
            if import_skipped:
                parsed["skipped"] = import_skipped

        # Allocate remedial classes automatically from ATTC Backlog Analysis
        batches_created = ai_engine.detect_failures_and_build_batches(dept_id, conn=conn)

        scheduled_count = 0
        if auto_schedule:
            scheduled_count = ai_engine.generate_timetable(dept_id, conn=conn)

        log_audit(
            user["id"],
            "IMPORT_ATTC_DATA",
            f"Imported records: {inserted_students} students, {inserted_backlogs} backlogs, {batches_created} batches, {scheduled_count} scheduled",
            conn=conn,
        )
        conn.commit()

    session.pop("attc_parsed", None)
    session.pop("attc_file_path", None)
    session.pop("is_attc", None)

    msg = f"Data successfully imported. Created/updated {batches_created} remedial batch(es)."
    if auto_schedule and scheduled_count > 0:
        msg += f" Auto-scheduled {scheduled_count} 3:30–4:30 PM routine slot(s)."
    flash(msg, "success")
    return redirect(url_for("admin.attc_backlog_analysis"))


@admin_bp.route("/attc-allocate-remedial", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def attc_allocate_remedial():
    """Trigger automatic remedial class allocation and schedule creation."""
    user = get_current_user()
    dept_id = _dept_scope(user)
    with get_db() as conn:
        batches_created = ai_engine.detect_failures_and_build_batches(dept_id, conn=conn)
        scheduled_count = ai_engine.generate_timetable(dept_id, conn=conn)
        log_audit(
            user["id"],
            "ALLOCATE_REMEDIAL",
            f"Allocated remedial classes: {batches_created} batches, {scheduled_count} scheduled",
            conn=conn,
        )
        conn.commit()
    flash(
        f"Remedial class allocation completed: {batches_created} batch(es) created/updated, {scheduled_count} timetable slot(s) scheduled.",
        "success",
    )
    return redirect(url_for("admin.attc_backlog_analysis"))


@admin_bp.route("/attc-set-threshold", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def attc_set_threshold():
    """Configure minimum attendance percentage threshold."""
    user = get_current_user()
    threshold_raw = request.form.get("threshold", "").strip()
    try:
        val = float(threshold_raw)
        if not (0 <= val <= 100):
            raise ValueError()
        with get_db() as conn:
            set_setting("min_attendance_threshold", f"{val:.1f}", conn=conn)
            log_audit(user["id"], "SET_THRESHOLD", f"Updated minimum attendance threshold to {val:.1f}%", conn=conn)
            conn.commit()
        flash(f"Minimum attendance threshold updated to {val:.1f}%.", "success")
    except ValueError:
        flash("Please enter a valid attendance percentage between 0 and 100.", "warning")
    return redirect(url_for("admin.attc_backlog_analysis"))


@admin_bp.route("/attc-export-weak-students")
@login_required(roles=["super_admin", "dept_admin"])
def attc_export_weak_students():
    """Export the Weak Student List to CSV with all 9 required columns."""
    user = get_current_user()
    dept_id = _dept_scope(user)
    threshold_param = request.args.get("threshold")
    with get_db() as conn:
        current_threshold = float(threshold_param) if threshold_param else float(
            get_setting("min_attendance_threshold", 75.0, conn=conn)
        )
        analysis = ai_engine.get_attc_backlog_analysis(
            dept_id, min_attendance_threshold=current_threshold, conn=conn
        )
        weak_students = analysis["weak_students"]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Student Name",
        "Roll Number",
        "Semester",
        "Attendance Percentage",
        "Academic Result Status",
        "Failed Subjects",
        "Total Backlogs",
        "Reason for Selection",
        "Remedial Class Status",
    ])
    for s in weak_students:
        writer.writerow([
            s["student_name"],
            s["roll_number"],
            s["semester"],
            s["attendance_percentage"],
            s["academic_result_status"],
            s["failed_subjects"],
            s["total_backlogs"],
            s["reason_for_selection"],
            s["remedial_class_status"],
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=weak_students_list_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"},
    )


@admin_bp.route("/batches")
@login_required(roles=["super_admin", "dept_admin"])
def batches():
    user = get_current_user()
    dept_id = _dept_scope(user)
    with get_db() as conn:
        query = """
            SELECT rb.*, s.name AS subject_name, s.code AS subject_code, d.code AS dept_code,
                   t.full_name AS teacher_name, c.room_code,
                   COUNT(DISTINCT rs.student_id) AS student_count,
                   (SELECT COUNT(*) FROM timetable tt WHERE tt.batch_id = rb.id) AS slot_count
            FROM remedial_batches rb
            JOIN subjects s ON s.id = rb.subject_id
            JOIN departments d ON d.id = rb.department_id
            LEFT JOIN teachers t ON t.id = rb.teacher_id
            LEFT JOIN classrooms c ON c.id = rb.classroom_id
            LEFT JOIN remedial_students rs ON rs.batch_id = rb.id
        """
        params = []
        if dept_id:
            query += " WHERE rb.department_id = ?"
            params.append(dept_id)
        query += " GROUP BY rb.id ORDER BY rb.created_at DESC"
        rows = conn.execute(query, params).fetchall()
    return render_template("admin/batches.html", user=user, batches=rows)


@admin_bp.route("/batch/<int:batch_id>", methods=["GET", "POST"])
@login_required(roles=["super_admin", "dept_admin"])
def batch_detail(batch_id):
    user = get_current_user()
    dept_id = _dept_scope(user)
    with get_db() as conn:
        query = """
            SELECT rb.*, s.name AS subject_name, s.code AS subject_code, d.code AS dept_code, d.name AS dept_name,
                   t.full_name AS teacher_name, c.room_code, c.capacity
            FROM remedial_batches rb
            JOIN subjects s ON s.id = rb.subject_id
            JOIN departments d ON d.id = rb.department_id
            LEFT JOIN teachers t ON t.id = rb.teacher_id
            LEFT JOIN classrooms c ON c.id = rb.classroom_id
            WHERE rb.id = ?
        """
        params = [batch_id]
        if dept_id:
            query += " AND rb.department_id = ?"
            params.append(dept_id)
        batch = conn.execute(query, params).fetchone()
        if not batch:
            flash("Batch not found.", "danger")
            return redirect(url_for("admin.batches"))

        if request.method == "POST":
            teacher_id = request.form.get("teacher_id") or None
            classroom_id = request.form.get("classroom_id") or None
            status = request.form.get("status", batch["status"])

            conn.execute(
                """
                UPDATE remedial_batches
                SET teacher_id = ?, classroom_id = ?, status = ?
                WHERE id = ?
                """,
                (teacher_id, classroom_id, status, batch_id),
            )
            # Update teacher/room in timetable if already scheduled
            if teacher_id and classroom_id:
                conn.execute(
                    """
                    UPDATE timetable
                    SET teacher_id = ?, classroom_id = ?
                    WHERE batch_id = ?
                    """,
                    (teacher_id, classroom_id, batch_id),
                )
            conn.commit()
            log_audit(user["id"], "UPDATE_BATCH", f"Batch {batch['batch_code']} updated")
            flash("Batch details updated successfully.", "success")
            return redirect(url_for("admin.batch_detail", batch_id=batch_id))

        students = conn.execute(
            """
            SELECT st.*, r.marks, r.pass_marks, r.result
            FROM remedial_students rs
            JOIN students st ON st.id = rs.student_id
            LEFT JOIN results r ON r.student_id = st.student_id
                 AND r.subject_id = ? AND r.semester = ?
            WHERE rs.batch_id = ?
            ORDER BY st.full_name
            """,
            (batch["subject_id"], batch["semester"], batch_id),
        ).fetchall()

        timetable_slots = conn.execute(
            """
            SELECT tt.*, c.room_code, te.full_name AS teacher_name
            FROM timetable tt
            JOIN classrooms c ON c.id = tt.classroom_id
            JOIN teachers te ON te.id = tt.teacher_id
            WHERE tt.batch_id = ?
            ORDER BY tt.day_of_week, tt.start_time
            """,
            (batch_id,),
        ).fetchall()

        teachers = conn.execute(
            "SELECT * FROM teachers WHERE department_id = ? ORDER BY full_name",
            (batch["department_id"],),
        ).fetchall()

        classrooms = conn.execute(
            "SELECT * FROM classrooms WHERE department_id = ? OR department_id IS NULL ORDER BY capacity",
            (batch["department_id"],),
        ).fetchall()

        peer_mentor = conn.execute(
            """
            SELECT pm.*, st.student_id AS mentor_student_id_text, st.full_name AS mentor_name
            FROM peer_mentors pm
            JOIN students st ON st.id = pm.mentor_student_id
            WHERE pm.batch_id = ?
            """,
            (batch_id,),
        ).fetchone()

    return render_template(
        "admin/batch_detail.html",
        user=user,
        batch=batch,
        students=students,
        timetable_slots=timetable_slots,
        teachers=teachers,
        classrooms=classrooms,
        peer_mentor=peer_mentor,
    )


@admin_bp.route("/timetable")
@login_required(roles=["super_admin", "dept_admin"])
def timetable():
    user = get_current_user()
    dept_id = _dept_scope(user)
    day_filter = request.args.get("day", "").strip()
    teacher_filter = request.args.get("teacher_id", "").strip()
    room_filter = request.args.get("room_id", "").strip()

    with get_db() as conn:
        query = """
            SELECT tt.*, rb.batch_code, rb.semester, rb.section, rb.academic_batch, s.name AS subject_name,
                   s.code AS subject_code, d.code AS dept_code,
                   te.full_name AS teacher_name, c.room_code, c.capacity,
                   (SELECT COUNT(*) FROM remedial_students rs WHERE rs.batch_id = rb.id) AS student_count
            FROM timetable tt
            JOIN remedial_batches rb ON rb.id = tt.batch_id
            JOIN subjects s ON s.id = rb.subject_id
            JOIN departments d ON d.id = rb.department_id
            LEFT JOIN teachers te ON te.id = tt.teacher_id
            LEFT JOIN classrooms c ON c.id = tt.classroom_id
            WHERE 1=1
        """
        params = []
        if dept_id:
            query += " AND rb.department_id = ?"
            params.append(dept_id)
        if day_filter:
            query += " AND tt.day_of_week = ?"
            params.append(day_filter)
        if teacher_filter:
            query += " AND tt.teacher_id = ?"
            params.append(int(teacher_filter))
        if room_filter:
            query += " AND tt.classroom_id = ?"
            params.append(int(room_filter))

        query += " ORDER BY CASE tt.day_of_week WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END, tt.start_time"
        entries = conn.execute(query, params).fetchall()
        entries = [
            {**dict(e), "start_time": ai_engine.normalize_time(e["start_time"]), "end_time": ai_engine.normalize_time(e["end_time"])}
            for e in entries
        ]

        # Data for modal / forms
        dept_clause = "WHERE department_id = ?" if dept_id else ""
        dept_params = [dept_id] if dept_id else []

        pending_batches = conn.execute(
            f"""
            SELECT rb.*, s.name AS subject_name
            FROM remedial_batches rb
            JOIN subjects s ON s.id = rb.subject_id
            WHERE rb.status = 'PENDING'
              AND NOT EXISTS (SELECT 1 FROM timetable tt WHERE tt.batch_id = rb.id)
              {"AND rb.department_id = ?" if dept_id else ""}
            ORDER BY rb.batch_code
            """,
            dept_params,
        ).fetchall()

        all_teachers = conn.execute(
            f"SELECT * FROM teachers {dept_clause} ORDER BY full_name",
            dept_params,
        ).fetchall()

        if dept_id:
            all_classrooms = conn.execute(
                "SELECT * FROM classrooms WHERE department_id = ? OR department_id IS NULL ORDER BY capacity, room_code",
                dept_params,
            ).fetchall()
        else:
            all_classrooms = conn.execute(
                "SELECT * FROM classrooms ORDER BY capacity, room_code"
            ).fetchall()

        active_slots = list(ai_engine.SLOTS)
        for e in entries:
            s_start = e["start_time"]
            s_end = e["end_time"]
            if s_start and not any(s[0] == s_start for s in active_slots):
                active_slots.append((s_start, s_end or ai_engine.SLOT_END.get(s_start, "")))
        active_slots.sort(key=lambda s: s[0])

    return render_template(
        "admin/timetable.html",
        user=user,
        entries=entries,
        days=ai_engine.DAYS,
        slots=active_slots,
        day_filter=day_filter,
        teacher_filter=int(teacher_filter) if teacher_filter else None,
        room_filter=int(room_filter) if room_filter else None,
        pending_batches=pending_batches,
        all_teachers=all_teachers,
        all_classrooms=all_classrooms,
    )


@admin_bp.route("/timetable/generate", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def generate_timetable_action():
    user = get_current_user()
    dept_id = _dept_scope(user)
    scheduled = ai_engine.generate_timetable(dept_id)
    log_audit(user["id"], "GENERATE_TIMETABLE", f"Scheduled {scheduled} batches")
    if scheduled > 0:
        flash(f"Successfully generated after-college routine (3:30–4:30 PM) for {scheduled} batch(es) without same-semester or teacher/room clashes.", "success")
    else:
        flash("No pending batches to schedule or all available weekday slots for this semester/teacher/room are occupied.", "info")
    return redirect(url_for("admin.timetable"))


@admin_bp.route("/timetable/add", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def add_timetable_slot():
    user = get_current_user()
    dept_id = _dept_scope(user)
    batch_id = int(request.form["batch_id"])
    day_of_week = request.form["day_of_week"].strip()
    start_time = ai_engine.normalize_time(request.form["start_time"].strip())
    end_time = ai_engine.normalize_time(
        request.form.get("end_time", "").strip() or ai_engine.SLOT_END.get(start_time, "16:30")
    )
    teacher_id = int(request.form["teacher_id"])
    classroom_id = int(request.form["classroom_id"])

    if not end_time:
        flash("Invalid time slot selected.", "danger")
        return redirect(url_for("admin.timetable"))

    with get_db() as conn:
        batch = conn.execute(
            "SELECT * FROM remedial_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if not batch:
            flash("Batch not found.", "danger")
            return redirect(url_for("admin.timetable"))
        if dept_id and batch["department_id"] != dept_id:
            flash("This batch is outside Computer Engineering scope.", "danger")
            return redirect(url_for("admin.timetable"))

        existing_batch_slot = conn.execute(
            "SELECT id FROM timetable WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if existing_batch_slot:
            flash("This batch already has a timetable slot. Edit or remove it first.", "warning")
            return redirect(url_for("admin.timetable"))

        # Teacher or Classroom clash check
        clash = conn.execute(
            """
            SELECT tt.*, rb.batch_code, te.full_name AS teacher_name, c.room_code
            FROM timetable tt
            JOIN remedial_batches rb ON rb.id = tt.batch_id
            JOIN teachers te ON te.id = tt.teacher_id
            JOIN classrooms c ON c.id = tt.classroom_id
            WHERE tt.day_of_week = ? AND tt.start_time = ?
              AND (tt.teacher_id = ? OR tt.classroom_id = ?)
            """,
            (day_of_week, start_time, teacher_id, classroom_id),
        ).fetchone()

        if clash:
            flash(
                f"Schedule Clash Detected! Slot {day_of_week} {start_time} is already booked by {clash['teacher_name']} in {clash['room_code']} for {clash['batch_code']}.",
                "danger",
            )
            return redirect(url_for("admin.timetable"))

        # Semester clash check: do not conduct same semester classes on the same day
        sem_clash = conn.execute(
            """
            SELECT tt.*, rb.batch_code, rb.semester, s.name AS subject_name
            FROM timetable tt
            JOIN remedial_batches rb ON rb.id = tt.batch_id
            JOIN subjects s ON s.id = rb.subject_id
            WHERE tt.day_of_week = ?
              AND rb.department_id = ?
              AND rb.semester = ?
            """,
            (day_of_week, batch["department_id"], batch["semester"]),
        ).fetchone()
        if sem_clash:
            flash(
                f"Semester Clash: Semester {batch['semester']} already has a remedial class on {day_of_week} ({sem_clash['batch_code']} - {sem_clash['subject_name']}). Cannot conduct same semester classes on the same day.",
                "danger",
            )
            return redirect(url_for("admin.timetable"))

        conn.execute(
            """
            INSERT INTO timetable (batch_id, day_of_week, start_time, end_time, classroom_id, teacher_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (batch_id, day_of_week, start_time, end_time, classroom_id, teacher_id),
        )
        conn.execute(
            """
            UPDATE remedial_batches
            SET teacher_id = ?, classroom_id = ?, status = 'SCHEDULED'
            WHERE id = ?
            """,
            (teacher_id, classroom_id, batch_id),
        )
        conn.commit()
        log_audit(user["id"], "ADD_TIMETABLE_SLOT", f"Batch {batch_id} slot added {day_of_week} {start_time}")
        flash("Routine slot added successfully.", "success")

    return redirect(url_for("admin.timetable"))


@admin_bp.route("/timetable/edit/<int:slot_id>", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def edit_timetable_slot(slot_id):
    user = get_current_user()
    dept_id = _dept_scope(user)
    day_of_week = request.form["day_of_week"].strip()
    start_time = ai_engine.normalize_time(request.form["start_time"].strip())
    end_time = ai_engine.normalize_time(
        request.form.get("end_time", "").strip() or ai_engine.SLOT_END.get(start_time, "16:30")
    )
    teacher_id = int(request.form["teacher_id"])
    classroom_id = int(request.form["classroom_id"])

    if not end_time:
        flash("Invalid time slot selected.", "danger")
        return redirect(url_for("admin.timetable"))

    with get_db() as conn:
        slot = conn.execute(
            """
            SELECT tt.*, rb.department_id, rb.batch_code, rb.semester
            FROM timetable tt
            JOIN remedial_batches rb ON rb.id = tt.batch_id
            WHERE tt.id = ?
            """,
            (slot_id,),
        ).fetchone()
        if not slot:
            flash("Timetable slot not found.", "danger")
            return redirect(url_for("admin.timetable"))
        if dept_id and slot["department_id"] != dept_id:
            flash("This slot is outside Computer Engineering scope.", "danger")
            return redirect(url_for("admin.timetable"))

        # Teacher or Classroom clash check
        clash = conn.execute(
            """
            SELECT tt.*, rb.batch_code, te.full_name AS teacher_name, c.room_code
            FROM timetable tt
            JOIN remedial_batches rb ON rb.id = tt.batch_id
            JOIN teachers te ON te.id = tt.teacher_id
            JOIN classrooms c ON c.id = tt.classroom_id
            WHERE tt.day_of_week = ? AND tt.start_time = ?
              AND (tt.teacher_id = ? OR tt.classroom_id = ?)
              AND tt.id != ?
            """,
            (day_of_week, start_time, teacher_id, classroom_id, slot_id),
        ).fetchone()
        if clash:
            flash(
                f"Schedule clash: {day_of_week} {start_time} already booked for {clash['batch_code']} ({clash['teacher_name']} in {clash['room_code']}).",
                "danger",
            )
            return redirect(url_for("admin.timetable"))

        # Semester clash check: do not conduct same semester classes on the same day
        sem_clash = conn.execute(
            """
            SELECT tt.*, rb.batch_code, rb.semester, s.name AS subject_name
            FROM timetable tt
            JOIN remedial_batches rb ON rb.id = tt.batch_id
            JOIN subjects s ON s.id = rb.subject_id
            WHERE tt.day_of_week = ?
              AND rb.department_id = ?
              AND rb.semester = ?
              AND tt.id != ?
            """,
            (day_of_week, slot["department_id"], slot["semester"], slot_id),
        ).fetchone()
        if sem_clash:
            flash(
                f"Semester Clash: Semester {slot['semester']} already has a remedial class on {day_of_week} ({sem_clash['batch_code']} - {sem_clash['subject_name']}). Cannot conduct same semester classes on the same day.",
                "danger",
            )
            return redirect(url_for("admin.timetable"))

        conn.execute(
            """
            UPDATE timetable
            SET day_of_week = ?, start_time = ?, end_time = ?,
                classroom_id = ?, teacher_id = ?
            WHERE id = ?
            """,
            (day_of_week, start_time, end_time, classroom_id, teacher_id, slot_id),
        )
        conn.execute(
            """
            UPDATE remedial_batches
            SET teacher_id = ?, classroom_id = ?, status = 'SCHEDULED'
            WHERE id = ?
            """,
            (teacher_id, classroom_id, slot["batch_id"]),
        )
        conn.commit()
        log_audit(user["id"], "EDIT_TIMETABLE_SLOT", f"Slot {slot_id} updated for {slot['batch_code']}")
        flash("Timetable slot updated successfully.", "success")

    return redirect(url_for("admin.timetable"))


@admin_bp.route("/timetable/delete/<int:slot_id>", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def delete_timetable_slot(slot_id):
    user = get_current_user()
    dept_id = _dept_scope(user)
    with get_db() as conn:
        slot = conn.execute(
            """
            SELECT tt.*, rb.department_id, rb.batch_code
            FROM timetable tt
            JOIN remedial_batches rb ON rb.id = tt.batch_id
            WHERE tt.id = ?
            """,
            (slot_id,),
        ).fetchone()
        if slot:
            if dept_id and slot["department_id"] != dept_id:
                flash("This slot is outside Computer Engineering scope.", "danger")
                return redirect(url_for("admin.timetable"))
            batch_id = slot["batch_id"]
            conn.execute("DELETE FROM timetable WHERE id = ?", (slot_id,))
            remaining = conn.execute(
                "SELECT COUNT(*) AS c FROM timetable WHERE batch_id = ?", (batch_id,)
            ).fetchone()["c"]
            if remaining == 0:
                conn.execute(
                    "UPDATE remedial_batches SET status = 'PENDING' WHERE id = ?", (batch_id,)
                )
            conn.commit()
            log_audit(user["id"], "DELETE_TIMETABLE_SLOT", f"Slot {slot_id} deleted")
            flash("Timetable slot removed.", "info")
    return redirect(url_for("admin.timetable"))


@admin_bp.route("/failure-list")
@login_required(roles=["super_admin", "dept_admin"])
def failure_list():
    user = get_current_user()
    dept_id = _dept_scope(user)
    q = request.args.get("q", "").strip()
    subject_q = request.args.get("subject", "").strip()
    semester = request.args.get("semester", "").strip()
    academic_batch = request.args.get("academic_batch", "").strip()

    with get_db() as conn:
        query = """
            SELECT r.student_id, r.student_name, r.semester, r.section,
                   r.academic_batch, r.marks, r.pass_marks, r.result,
                   s.name AS subject_name, d.code AS dept_code
            FROM results r
            JOIN subjects s ON s.id = r.subject_id
            JOIN departments d ON d.id = r.department_id
            WHERE r.result = 'FAIL'
        """
        params = []
        if dept_id:
            query += " AND r.department_id = ?"
            params.append(dept_id)
        if q:
            query += " AND (r.student_id LIKE ? OR r.student_name LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%"])
        if subject_q:
            query += " AND s.name LIKE ?"
            params.append(f"%{subject_q}%")
        if semester:
            query += " AND r.semester = ?"
            params.append(int(semester))
        if academic_batch:
            query += " AND r.academic_batch = ?"
            params.append(academic_batch)
        query += " ORDER BY r.academic_batch, r.semester DESC, r.student_name, s.name"
        rows = conn.execute(query, params).fetchall()

        totals_query = """
            SELECT COALESCE(r.academic_batch, 'Unknown') AS academic_batch,
                   r.semester,
                   COUNT(*) AS fail_count,
                   COUNT(DISTINCT r.student_id) AS student_count
            FROM results r
            WHERE r.result = 'FAIL'
        """
        totals_params = []
        if dept_id:
            totals_query += " AND r.department_id = ?"
            totals_params.append(dept_id)
        totals_query += " GROUP BY r.academic_batch, r.semester ORDER BY r.academic_batch, r.semester"
        batch_totals = conn.execute(totals_query, totals_params).fetchall()

        batch_query = "SELECT DISTINCT academic_batch FROM results WHERE result = 'FAIL' AND academic_batch IS NOT NULL AND academic_batch != ''"
        batch_params = []
        if dept_id:
            batch_query += " AND department_id = ?"
            batch_params.append(dept_id)
        batch_query += " ORDER BY academic_batch"
        stored_batches = [r["academic_batch"] for r in conn.execute(batch_query, batch_params).fetchall()]
        academic_batches = list(dict.fromkeys(ACADEMIC_BATCHES + stored_batches))

    return render_template(
        "admin/failure_list.html",
        user=user,
        failures=rows,
        total=len(rows),
        unique_students=len({r["student_id"] for r in rows}),
        q=q,
        subject_q=subject_q,
        semester=int(semester) if semester else None,
        academic_batch=academic_batch or None,
        academic_batches=academic_batches,
        ACADEMIC_BATCHES=ACADEMIC_BATCHES,
        semesters=SEMESTERS,
        batch_totals=batch_totals,
    )


@admin_bp.route("/failure-list/export")
@login_required(roles=["super_admin", "dept_admin"])
def export_failures():
    user = get_current_user()
    dept_id = _dept_scope(user)
    semester = request.args.get("semester", "").strip()
    academic_batch = request.args.get("academic_batch", "").strip()
    with get_db() as conn:
        query = """
            SELECT r.student_id, r.student_name, d.code AS department, r.semester,
                   r.academic_batch, r.section, s.name AS subject_name, r.marks, r.pass_marks, r.result
            FROM results r
            JOIN subjects s ON s.id = r.subject_id
            JOIN departments d ON d.id = r.department_id
            WHERE r.result = 'FAIL'
        """
        params = []
        if dept_id:
            query += " AND r.department_id = ?"
            params.append(dept_id)
        if semester:
            query += " AND r.semester = ?"
            params.append(int(semester))
        if academic_batch:
            query += " AND r.academic_batch = ?"
            params.append(academic_batch)
        query += " ORDER BY r.academic_batch, r.semester, r.student_name"
        rows = conn.execute(query, params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Student ID", "Student Name", "Department", "Academic Batch", "Semester", "Subject", "Result"])
    for r in rows:
        result = "Pass" if r["result"] == "PASS" else "Fail"
        writer.writerow([
            r["student_id"], r["student_name"], r["department"], r["academic_batch"],
            r["semester"], r["subject_name"], result,
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=failure_list_{datetime.now().strftime('%Y%m%d')}.csv"},
    )


@admin_bp.route("/students")
@login_required(roles=["super_admin", "dept_admin"])
def students():
    user = get_current_user()
    dept_id = _dept_scope(user)
    q = request.args.get("q", "").strip()
    academic_batch = request.args.get("academic_batch", "").strip()
    with get_db() as conn:
        threshold = float(get_setting("min_attendance_threshold", 75, conn=conn))
        all_students = ai_engine.identify_weak_students(dept_id, min_attendance_threshold=threshold, conn=conn)
        filtered = []
        for s in all_students:
            if q:
                student_id_text = str(s.get("student_id") or "").lower()
                full_name_text = str(s.get("full_name") or "").lower()
                if q.lower() not in student_id_text and q.lower() not in full_name_text:
                    continue
            if academic_batch:
                if s.get("academic_batch") != academic_batch:
                    continue
            filtered.append(s)

        filtered.sort(key=lambda s: (not s["is_weak"], s.get("academic_batch") or "", s.get("full_name") or ""))
        weak_students = [s for s in filtered if s["is_weak"]]

    return render_template(
        "admin/students.html",
        user=user,
        students=filtered,
        weak_students=weak_students,
        q=q,
        academic_batch=academic_batch or None,
        academic_batches=ACADEMIC_BATCHES,
        low_threshold=threshold,
    )


@admin_bp.route("/teachers", methods=["GET", "POST"])
@login_required(roles=["super_admin", "dept_admin"])
def teachers():
    user = get_current_user()
    dept_id = _dept_scope(user)
    if request.method == "POST":
        eid = request.form.get("employee_id", "").strip()
        name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        department = dept_id or request.form.get("department_id")

        if not eid or not name or not department:
            flash("Please fill in all required teacher fields.", "warning")
            return redirect(url_for("admin.teachers"))

        with get_db() as conn:
            existing = conn.execute("SELECT id FROM teachers WHERE employee_id = ?", (eid,)).fetchone()
            if existing:
                flash(f"Teacher with Employee ID {eid} already exists.", "danger")
                return redirect(url_for("admin.teachers"))
            username = eid.lower()
            existing_user = conn.execute("SELECT id, role FROM users WHERE LOWER(username) = ?", (username,)).fetchone()
            if existing_user:
                flash(f"The employee ID {eid} is already linked to another account.", "danger")
                return redirect(url_for("admin.teachers"))

            cur = conn.execute(
                "INSERT INTO teachers (employee_id, full_name, department_id, email) VALUES (?, ?, ?, ?)",
                (eid, name, department, email if email else None),
            )
            teacher_pk = cur.lastrowid
            conn.execute(
                """
                INSERT INTO users (username, password_hash, role, full_name, department_id)
                VALUES (?, ?, 'teacher', ?, ?)
                """,
                (username, hash_password("teacher123"), name, department),
            )
            user_row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if user_row:
                conn.execute(
                    "UPDATE teachers SET user_id = ? WHERE id = ?",
                    (user_row["id"], teacher_pk),
                )
            conn.commit()
            log_audit(user["id"], "ADD_TEACHER", f"Added teacher {eid} - {name}")
        flash(f"Teacher {name} ({eid}) added successfully. Login: {username} / teacher123", "success")
        return redirect(url_for("admin.teachers"))

    with get_db() as conn:
        query = """
            SELECT t.*, d.code AS dept_code, d.name AS dept_name,
                   (SELECT COUNT(*) FROM remedial_batches rb WHERE rb.teacher_id = t.id) AS assigned_batches
            FROM teachers t
            JOIN departments d ON d.id = t.department_id
        """
        params = []
        if dept_id:
            query += " WHERE t.department_id = ?"
            params.append(dept_id)
        rows = conn.execute(query, params).fetchall()
        departments = conn.execute("SELECT id, code, name FROM departments ORDER BY name").fetchall()
    return render_template(
        "admin/teachers.html", user=user, teachers=rows, departments=departments
    )


@admin_bp.route("/analytics")
@login_required(roles=["super_admin", "dept_admin"])
def analytics():
    user = get_current_user()
    data = ai_engine.get_analytics(_dept_scope(user))
    return render_template("admin/analytics.html", user=user, analytics=data)


@admin_bp.route("/departments", methods=["GET", "POST"])
@login_required(roles=["super_admin"])
def departments():
    """Super Admin control panel for departments and department administrators."""
    if request.method == "POST":
        action = request.form.get("action", "department")
        with get_db() as conn:
            if action == "admin":
                username = request.form.get("username", "").strip().lower()
                password = request.form.get("password", "")
                full_name = request.form.get("full_name", "").strip()
                email = request.form.get("email", "").strip()
                department_id = request.form.get("department_id", type=int)
                if not username or len(password) < 4 or not full_name or not department_id:
                    flash("Enter a name, department, username, and password of at least 4 characters.", "warning")
                    return redirect(url_for("admin.departments"))
                if conn.execute("SELECT 1 FROM users WHERE LOWER(username) = ?", (username,)).fetchone():
                    flash("That username is already in use.", "danger")
                    return redirect(url_for("admin.departments"))
                conn.execute("INSERT INTO users (username, password_hash, role, full_name, department_id, email) VALUES (?, ?, 'dept_admin', ?, ?, ?)",
                             (username, hash_password(password), full_name, department_id, email if email else None))
                conn.commit()
                flash(f"Department admin {username} created. They can sign in with that username and password.", "success")
            elif action == "teacher":
                username = request.form.get("username", "").strip().lower()
                password = request.form.get("password", "")
                full_name = request.form.get("full_name", "").strip()
                employee_id = request.form.get("employee_id", "").strip()
                email = request.form.get("email", "").strip()
                department_id = request.form.get("department_id", type=int)
                if not username or len(password) < 4 or not full_name or not employee_id or not department_id:
                    flash("Enter all required fields: username, password, full name, employee ID, and department.", "warning")
                    return redirect(url_for("admin.departments"))
                if conn.execute("SELECT 1 FROM users WHERE LOWER(username) = ?", (username,)).fetchone():
                    flash("That username is already in use.", "danger")
                    return redirect(url_for("admin.departments"))
                if conn.execute("SELECT 1 FROM teachers WHERE employee_id = ?", (employee_id,)).fetchone():
                    flash("That employee ID is already in use.", "danger")
                    return redirect(url_for("admin.departments"))
                cur = conn.execute(
                    "INSERT INTO teachers (employee_id, full_name, department_id, email) VALUES (?, ?, ?, ?)",
                    (employee_id, full_name, department_id, email if email else None),
                )
                teacher_pk = cur.lastrowid
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, full_name, department_id) VALUES (?, ?, 'teacher', ?, ?)",
                    (username, hash_password(password), full_name, department_id),
                )
                user_row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
                if user_row:
                    conn.execute(
                        "UPDATE teachers SET user_id = ? WHERE id = ?",
                        (user_row["id"], teacher_pk),
                    )
                conn.commit()
                flash(f"Teacher {full_name} ({employee_id}) added successfully. Login: {username} / {password}", "success")
            else:
                code = request.form.get("code", "").strip().upper()
                name = request.form.get("name", "").strip()
                if not code or not name:
                    flash("Department code and name are required.", "warning")
                    return redirect(url_for("admin.departments"))
                try:
                    conn.execute("INSERT INTO departments (code, name) VALUES (?, ?)", (code, name))
                    conn.commit()
                    flash("Department created successfully.", "success")
                except sqlite3.IntegrityError:
                    flash("A department with that code already exists.", "danger")
        return redirect(url_for("admin.departments"))

    with get_db() as conn:
        department_rows = conn.execute("""
            SELECT d.*,
                   (SELECT COUNT(*) FROM users u WHERE u.department_id = d.id AND u.role = 'dept_admin') AS admin_count,
                   (SELECT COUNT(*) FROM teachers t WHERE t.department_id = d.id) AS teacher_count,
                   (SELECT COUNT(*) FROM students s WHERE s.department_id = d.id) AS student_count,
                   (SELECT COUNT(*) FROM remedial_batches rb WHERE rb.department_id = d.id) AS batch_count,
                   (SELECT COUNT(*) FROM timetable tt JOIN remedial_batches rb ON rb.id = tt.batch_id WHERE rb.department_id = d.id) AS scheduled_count,
                   COALESCE((SELECT AVG(sa.attendance_percent) FROM semester_attendance sa JOIN students s ON s.id = sa.student_id WHERE s.department_id = d.id), 0) AS avg_attendance,
                   (SELECT COUNT(*) FROM results r WHERE r.department_id = d.id AND r.result = 'FAIL') AS failure_count
            FROM departments d ORDER BY d.name
        """).fetchall()
        admins = conn.execute("""
            SELECT u.id, u.username, u.full_name, d.code, u.is_active FROM users u
            JOIN departments d ON d.id = u.department_id WHERE u.role = 'dept_admin' ORDER BY d.code, u.full_name
        """).fetchall()
        teachers = conn.execute("""
            SELECT t.id, t.employee_id, t.full_name, d.code, u.username FROM teachers t
            JOIN departments d ON d.id = t.department_id
            LEFT JOIN users u ON u.id = t.user_id ORDER BY d.code, t.full_name
        """).fetchall()
    return render_template("admin/departments.html", user=get_current_user(), departments=department_rows, admins=admins, teachers=teachers)


@admin_bp.route("/department-admin/<int:admin_id>/reset-password", methods=["POST"])
@login_required(roles=["super_admin"])
def reset_department_admin_password(admin_id):
    """Super Admin securely resets a department administrator's password."""
    user = get_current_user()
    new_password = request.form.get("new_password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()

    if len(new_password) < 6:
        flash("Password must be at least 6 characters.", "warning")
        return redirect(url_for("admin.departments"))
    if new_password != confirm_password:
        flash("Passwords do not match.", "warning")
        return redirect(url_for("admin.departments"))

    with get_db() as conn:
        admin = conn.execute(
            """
            SELECT u.id, u.username, u.full_name, d.code AS dept_code
            FROM users u
            JOIN departments d ON d.id = u.department_id
            WHERE u.id = ? AND u.role = 'dept_admin'
            """,
            (admin_id,),
        ).fetchone()
        if not admin:
            flash("Department administrator not found.", "danger")
            return redirect(url_for("admin.departments"))

        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), admin_id),
        )
        conn.commit()
        log_audit(
            user["id"],
            "RESET_DEPT_ADMIN_PASSWORD",
            f"Reset password for dept admin {admin['username']} ({admin['dept_code']})",
        )
        flash(
            f"Password reset successfully for {admin['full_name']} ({admin['username']}). "
            "Share the new password securely with the administrator.",
            "success",
        )

    return redirect(url_for("admin.departments"))


@admin_bp.route("/failed-logins")
@login_required(roles=["super_admin", "dept_admin"])
def failed_logins():
    """View recent failed login attempts (admin review)."""
    user = get_current_user()
    dept_id = _dept_scope(user)
    with get_db() as conn:
        if user["role"] == "super_admin":
            rows = conn.execute(
                """
                SELECT * FROM failed_logins
                ORDER BY attempted_at DESC
                LIMIT 200
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM failed_logins
                ORDER BY attempted_at DESC
                LIMIT 200
                """
            ).fetchall()
    return render_template("admin/failed_logins.html", user=user, failed_logins=rows)


@admin_bp.route("/clear-database", methods=["GET", "POST"])
@login_required(roles=["super_admin"])
def clear_database():
    """Secure database clearing for Super Admin only."""
    if request.method == "POST":
        confirmation = request.form.get("confirmation", "").strip()
        if confirmation != "DELETE ALL DATA":
            flash("Invalid confirmation. Database was not cleared.", "danger")
            return redirect(url_for("admin.clear_database"))
        
        user = get_current_user()
        with get_db() as conn:
            # Get counts before deletion for logging
            student_count = conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"]
            teacher_count = conn.execute("SELECT COUNT(*) AS c FROM teachers").fetchone()["c"]
            batch_count = conn.execute("SELECT COUNT(*) AS c FROM remedial_batches").fetchone()["c"]

            # Preserve Super Admin account (departments are automatically preserved - not in deletion list)
            super_admin_id = conn.execute("SELECT id FROM users WHERE role = 'super_admin' LIMIT 1").fetchone()

            # Begin transaction
            try:
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

                for table in tables_to_clear:
                    conn.execute(f"DELETE FROM {table}")

                # Ensure Super Admin exists and is active
                if super_admin_id:
                    conn.execute("UPDATE users SET is_active = 1 WHERE id = ?", (super_admin_id["id"],))
                else:
                    conn.execute(
                        "INSERT INTO users (username, password_hash, role, full_name, department_id, is_active) VALUES (?, ?, 'super_admin', 'Super Admin', NULL, 1)",
                        ("superadmin", hash_password("admin123"),)
                    )

                conn.commit()
                log_audit(user["id"], "CLEAR_DATABASE", f"Cleared database: {student_count} students, {teacher_count} teachers, {batch_count} batches deleted")
                flash(f"Database cleared successfully. Deleted {student_count} students, {teacher_count} teachers, {batch_count} batches. Departments and system configuration preserved.", "success")
            except Exception as e:
                conn.rollback()
                flash(f"Error clearing database: {str(e)}", "danger")
                return redirect(url_for("admin.clear_database"))
        
        return redirect(url_for("admin.dashboard"))
    
    # GET request - show confirmation page
    with get_db() as conn:
        counts = {
            "students": conn.execute("SELECT COUNT(*) AS c FROM students").fetchone()["c"],
            "teachers": conn.execute("SELECT COUNT(*) AS c FROM teachers").fetchone()["c"],
            "batches": conn.execute("SELECT COUNT(*) AS c FROM remedial_batches").fetchone()["c"],
            "results": conn.execute("SELECT COUNT(*) AS c FROM results").fetchone()["c"],
            "attendance": conn.execute("SELECT COUNT(*) AS c FROM attendance").fetchone()["c"],
        }
    
    return render_template("admin/clear_database.html", user=get_current_user(), counts=counts)


@admin_bp.route("/semester-attendance", methods=["GET", "POST"])
@login_required(roles=["super_admin", "dept_admin"])
def semester_attendance():
    user = get_current_user()
    dept_id = _dept_scope(user)

    # Ensure session is permanent to prevent expiration during file upload workflow
    session.permanent = True

    with get_db() as conn:
        batch_query = "SELECT DISTINCT academic_batch FROM students WHERE academic_batch IS NOT NULL AND academic_batch != ''"
        batch_params = []
        if dept_id:
            batch_query += " AND department_id = ?"
            batch_params.append(dept_id)
        batch_query += " ORDER BY academic_batch"
        stored_batches = [r["academic_batch"] for r in conn.execute(batch_query, batch_params).fetchall()]
        academic_batches = list(dict.fromkeys(ACADEMIC_BATCHES + stored_batches))

        if request.method == "POST":
            file = request.files.get("file")
            semester = request.form.get("semester", type=int)
            academic_batch = request.form.get("academic_batch", "").strip()

            if not file or not file.filename:
                flash("Please upload a valid attendance sheet (.xlsx, .xls, .xlsm or .csv).", "warning")
                return redirect(url_for("admin.semester_attendance"))

            if not semester or not (1 <= semester <= 6):
                flash("Please select a valid semester.", "warning")
                return redirect(url_for("admin.semester_attendance"))

            filename = secure_filename(file.filename)
            ext = filename.rsplit(".", 1)[-1].lower()
            if ext not in {"xlsx", "xls", "xlsm", "csv"}:
                flash("Only .xlsx, .xls, .xlsm and .csv attendance sheets are supported.", "danger")
                return redirect(url_for("admin.semester_attendance"))

            path = UPLOAD_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_att_{filename}"
            file.save(path)

            try:
                parsed = parse_attendance_file(path, ext)
                att_records = parsed.get("records") or []
                if not att_records:
                    flash("The uploaded file has no attendance records. Please check the file format.", "warning")
                    return redirect(url_for("admin.semester_attendance"))

                # Show preview before import
                session["attendance_parsed"] = {
                    "records": att_records,
                    "semester": semester,
                    "academic_batch": academic_batch,
                    "format": parsed.get("format", "unknown"),
                    "students_found": parsed.get("students_found", 0),
                    "subjects_found": parsed.get("subjects_found", 0),
                    "skipped": parsed.get("skipped", []),
                }
                session["attendance_file_path"] = str(path)
                
                flash(
                    f"Preview: Found {parsed.get('students_found', 0)} students with attendance in {parsed.get('subjects_found', 0)} subjects. Review and confirm import.",
                    "info",
                )
                return redirect(url_for("admin.semester_attendance_preview"))
            except Exception as e:
                flash(f"Error reading attendance sheet: {str(e)}", "danger")
                return redirect(url_for("admin.semester_attendance"))

        filter_batch = request.args.get("academic_batch", "").strip()
        filter_sem = request.args.get("semester", type=int)
        just_uploaded = request.args.get("uploaded") == "1"

        # Get dynamic threshold from settings (default to 75%)
        low_threshold = float(get_setting("min_attendance_threshold", 75, conn=conn))

        query = """
            SELECT sa.*, st.student_id AS student_id_text, st.full_name,
                   sub.name AS subject_name, sub.code AS subject_code
            FROM semester_attendance sa
            JOIN students st ON st.id = sa.student_id
            LEFT JOIN subjects sub ON sub.id = sa.subject_id
            WHERE 1=1
        """
        params = []
        if dept_id:
            query += " AND st.department_id = ?"
            params.append(dept_id)
        if filter_batch:
            query += " AND sa.academic_batch = ?"
            params.append(filter_batch)
        if filter_sem:
            query += " AND sa.semester = ?"
            params.append(filter_sem)

        query += " ORDER BY sa.attendance_percent ASC, sa.semester, st.student_id"
        records = conn.execute(query, params).fetchall()
        low_attendance = [r for r in records if (r["attendance_percent"] or 0) < low_threshold]

    return render_template(
        "admin/semester_attendance.html",
        user=user,
        records=records,
        low_attendance=low_attendance,
        academic_batches=academic_batches,
        ACADEMIC_BATCHES=ACADEMIC_BATCHES,
        filter_batch=filter_batch,
        filter_sem=filter_sem,
        just_uploaded=just_uploaded,
        low_threshold=low_threshold,
    )


@admin_bp.route("/semester-attendance-preview", methods=["GET", "POST"])
@login_required(roles=["super_admin", "dept_admin"])
def semester_attendance_preview():
    """Preview and confirm attendance import."""
    user = get_current_user()
    dept_id = _dept_scope(user)

    # Ensure session is permanent to prevent expiration during file upload workflow
    session.permanent = True

    parsed = session.get("attendance_parsed")
    file_path = session.get("attendance_file_path")

    if not parsed or not file_path:
        flash("Session expired. Please upload the file again.", "warning")
        return redirect(url_for("admin.semester_attendance"))
    
    if request.method == "POST":
        # Confirm import
        with get_db() as conn:
            imported = 0
            missing_students = []
            low_attendance_students = []
            invalid_rows = []
            db_errors = []

            # Get dynamic threshold from settings (default to 75%)
            low_threshold = float(get_setting("min_attendance_threshold", 75, conn=conn))

            try:
                for rec in parsed["records"]:
                    sid_raw = str(rec.get("student_id") or "").strip()
                    if not sid_raw:
                        invalid_rows.append(f"Empty student ID in record")
                        continue

                    # Try multiple matching strategies
                    st = None

                    # Strategy 1: Exact match (case-insensitive)
                    st = conn.execute(
                        """
                        SELECT id, semester, academic_batch, department_id, student_id
                        FROM students WHERE UPPER(TRIM(student_id)) = UPPER(TRIM(?))
                        """,
                        (sid_raw,),
                    ).fetchone()

                    # Strategy 2: Try with spaces removed (e.g., "A24 DCE 18" -> "A24DCE18")
                    if not st:
                        sid_no_spaces = sid_raw.replace(" ", "").replace("\t", "")
                        st = conn.execute(
                            """
                            SELECT id, semester, academic_batch, department_id, student_id
                            FROM students WHERE UPPER(TRIM(student_id)) = UPPER(TRIM(?))
                            """,
                            (sid_no_spaces,),
                        ).fetchone()

                    # Strategy 3: Try removing special characters
                    if not st:
                        import re
                        sid_clean = re.sub(r'[^A-Za-z0-9]', '', sid_raw)
                        st = conn.execute(
                            """
                            SELECT id, semester, academic_batch, department_id, student_id
                            FROM students WHERE UPPER(TRIM(student_id)) = UPPER(TRIM(?))
                            """,
                            (sid_clean,),
                        ).fetchone()

                    if not st:
                        missing_students.append(sid_raw)
                        continue
                    if dept_id and st["department_id"] != dept_id:
                        continue

                    # Use exact subject name and code from the uploaded file
                    subject_name = rec.get("subject")
                    subject_code = rec.get("subject_code")
                    
                    subject_id = _get_or_create_subject(
                        conn,
                        subject_name,
                        subject_code,
                        st["department_id"],
                    )
                    batch_value = parsed["academic_batch"] or st["academic_batch"]
                    pct = float(rec.get("attendance_percent") or 0)
                    total_c = int(rec.get("total_classes") or 0)
                    attended_c = int(rec.get("classes_attended") or 0)

                    # Validate attendance data
                    if total_c < 0 or attended_c < 0 or attended_c > total_c:
                        invalid_rows.append(f"Invalid attendance data for {sid_raw}: {attended_c}/{total_c}")
                        continue

                    try:
                        conn.execute(
                            """
                            INSERT INTO semester_attendance
                            (student_id, semester, academic_batch, subject_id,
                             attendance_percent, total_classes, classes_attended, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                            ON CONFLICT(student_id, semester, academic_batch, subject_id)
                            DO UPDATE SET attendance_percent = excluded.attendance_percent,
                                          total_classes = excluded.total_classes,
                                          classes_attended = excluded.classes_attended,
                                          updated_at = excluded.updated_at
                            """,
                            (st["id"], parsed["semester"], batch_value, subject_id, pct, total_c, attended_c),
                        )
                        imported += 1
                    except Exception as e:
                        db_errors.append(f"Database error for {sid_raw}: {str(e)}")
                        continue

                    # Track low attendance students using dynamic threshold
                    if pct < low_threshold:
                        low_attendance_students.append(sid_raw)

                conn.commit()

                # Automatically identify remedial candidates based on low attendance
                low_count = len(low_attendance_students)

                # Trigger remedial batch creation for low attendance students
                if low_count > 0:
                    try:
                        batches_created = ai_engine.detect_failures_and_build_batches(dept_id, conn=conn)
                        log_audit(user["id"], "AUTO_ALLOCATE_REMEDIAL", f"Created {batches_created} remedial batches for low attendance students")
                    except Exception as e:
                        log_audit(user["id"], "AUTO_ALLOCATE_REMEDIAL_ERROR", f"Error creating remedial batches: {str(e)}")

                # Build detailed summary
                summary_parts = []
                summary_parts.append(f"Successfully imported {imported} attendance record(s)")

                if missing_students:
                    unique_missing = list(dict.fromkeys(missing_students))
                    summary_parts.append(f"{len(unique_missing)} student ID(s) not found: {', '.join(unique_missing[:5])}" + (f" and {len(unique_missing) - 5} more" if len(unique_missing) > 5 else ""))

                if invalid_rows:
                    unique_invalid = list(dict.fromkeys(invalid_rows))
                    summary_parts.append(f"{len(unique_invalid)} invalid row(s)")

                if db_errors:
                    unique_errors = list(dict.fromkeys(db_errors))
                    summary_parts.append(f"{len(unique_errors)} database error(s)")

                if low_count > 0:
                    summary_parts.append(f"{low_count} student(s) below {low_threshold}% identified for remedial support")

                flash(
                    ". ".join(summary_parts) + ".",
                    "success" if imported > 0 and not missing_students and not db_errors else "warning" if imported > 0 else "danger",
                )
                log_audit(
                    user["id"],
                    "UPLOAD_ATTENDANCE",
                    f"Uploaded semester {parsed['semester']} attendance ({imported} imported, {len(missing_students)} missing, {low_count} low attendance)",
                )

            except Exception as e:
                conn.rollback()
                flash(f"Error during import: {str(e)}. No data was saved.", "danger")
                log_audit(user["id"], "UPLOAD_ATTENDANCE_ERROR", f"Error importing attendance: {str(e)}")
                return redirect(url_for("admin.semester_attendance"))
        
        session.pop("attendance_parsed", None)
        session.pop("attendance_file_path", None)
        
        return redirect(url_for(
            "admin.semester_attendance",
            academic_batch=parsed["academic_batch"],
            semester=parsed["semester"],
            uploaded=1,
        ))
    
    # GET request - show preview
    sample_records = parsed["records"][:10]

    # Get dynamic threshold from settings (default to 75%)
    with get_db() as conn:
        low_threshold = float(get_setting("min_attendance_threshold", 75, conn=conn))

    return render_template(
        "admin/semester_attendance_preview.html",
        user=user,
        parsed=parsed,
        sample_records=sample_records,
        low_threshold=low_threshold,
    )


@admin_bp.route("/semester-attendance/delete/<int:record_id>", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def delete_semester_attendance(record_id):
    """Delete a specific semester attendance record."""
    user = get_current_user()
    dept_id = _dept_scope(user)
    
    with get_db() as conn:
        # Check if record exists and user has permission
        record = conn.execute(
            """
            SELECT sa.*, st.department_id
            FROM semester_attendance sa
            JOIN students st ON st.id = sa.student_id
            WHERE sa.id = ?
            """,
            (record_id,)
        ).fetchone()
        
        if not record:
            flash("Attendance record not found.", "danger")
            return redirect(url_for("admin.semester_attendance"))
        
        if dept_id and record["department_id"] != dept_id:
            flash("You don't have permission to delete this record.", "danger")
            return redirect(url_for("admin.semester_attendance"))
        
        conn.execute("DELETE FROM semester_attendance WHERE id = ?", (record_id,))
        conn.commit()
        log_audit(user["id"], "DELETE_SEMESTER_ATTENDANCE", f"Deleted attendance record {record_id}")
        
        flash("Attendance record deleted successfully.", "success")
    
    return redirect(url_for("admin.semester_attendance"))


@admin_bp.route("/semester-attendance/clear", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def clear_semester_attendance():
    """Clear all semester attendance records with optional filters."""
    user = get_current_user()
    dept_id = _dept_scope(user)
    
    academic_batch = request.form.get("academic_batch", "").strip()
    semester = request.form.get("semester", type=int)
    
    with get_db() as conn:
        query = """
            DELETE FROM semester_attendance
            WHERE id IN (
                SELECT sa.id FROM semester_attendance sa
                JOIN students st ON st.id = sa.student_id
                WHERE 1=1
        """
        params = []
        
        if dept_id:
            query += " AND st.department_id = ?"
            params.append(dept_id)
        if academic_batch:
            query += " AND sa.academic_batch = ?"
            params.append(academic_batch)
        if semester:
            query += " AND sa.semester = ?"
            params.append(semester)
        
        query += ")"
        
        cursor = conn.execute(query, params)
        deleted_count = cursor.rowcount
        conn.commit()
        
        log_audit(
            user["id"],
            "CLEAR_SEMESTER_ATTENDANCE",
            f"Cleared {deleted_count} attendance records (batch={academic_batch or 'all'}, semester={semester or 'all'})"
        )
        
        flash(f"Deleted {deleted_count} attendance record(s).", "success")
    
    return redirect(url_for("admin.semester_attendance"))


@admin_bp.route("/peer-mentors")
@login_required(roles=["super_admin", "dept_admin"])
def peer_mentors():
    user = get_current_user()
    dept_id = _dept_scope(user)

    with get_db() as conn:
        mentors_query = """
            SELECT pm.*, st.student_id AS mentor_student_id_text, st.full_name AS mentor_name,
                   st.semester AS mentor_semester, st.section AS mentor_section,
                   rb.batch_code, rb.semester, s.name AS subject_name, s.code AS subject_code,
                   (SELECT COUNT(*) FROM remedial_students rs WHERE rs.batch_id = pm.batch_id) AS mentee_count
            FROM peer_mentors pm
            JOIN students st ON st.id = pm.mentor_student_id
            JOIN remedial_batches rb ON rb.id = pm.batch_id
            JOIN subjects s ON s.id = rb.subject_id
            WHERE 1=1
        """
        mentors_params = []
        if dept_id:
            mentors_query += " AND rb.department_id = ?"
            mentors_params.append(dept_id)
        mentors_query += " ORDER BY pm.assigned_at DESC"
        mentors = conn.execute(mentors_query, mentors_params).fetchall()

        unmentored_query = """
            SELECT rb.id, rb.batch_code, rb.semester, s.name AS subject_name,
                   (SELECT COUNT(*) FROM remedial_students rs WHERE rs.batch_id = rb.id) AS student_count
            FROM remedial_batches rb
            JOIN subjects s ON s.id = rb.subject_id
            LEFT JOIN peer_mentors pm ON pm.batch_id = rb.id
            WHERE pm.id IS NULL
        """
        unmentored_params = []
        if dept_id:
            unmentored_query += " AND rb.department_id = ?"
            unmentored_params.append(dept_id)
        unmentored_query += " ORDER BY rb.batch_code"
        unmentored_batches = conn.execute(unmentored_query, unmentored_params).fetchall()

        toppers_query = """
            SELECT st.id, st.student_id, st.full_name, st.semester, st.section, st.cgpa,
                   AVG(r.marks) AS avg_marks,
                   COUNT(CASE WHEN r.result = 'FAIL' THEN 1 END) AS fail_count
            FROM students st
            LEFT JOIN results r ON UPPER(r.student_id) = UPPER(st.student_id)
            WHERE 1=1
        """
        toppers_params = []
        if dept_id:
            toppers_query += " AND st.department_id = ?"
            toppers_params.append(dept_id)
        toppers_query += " GROUP BY st.id HAVING fail_count = 0 ORDER BY st.cgpa DESC, avg_marks DESC, st.student_id ASC LIMIT 30"
        eligible_mentors = conn.execute(toppers_query, toppers_params).fetchall()

    return render_template(
        "admin/peer_mentors.html",
        user=user,
        mentors=mentors,
        unmentored_batches=unmentored_batches,
        eligible_mentors=eligible_mentors,
    )


@admin_bp.route("/peer-mentors/auto-assign", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def auto_assign_mentors():
    user = get_current_user()
    dept_id = _dept_scope(user)
    with get_db() as conn:
        count = ai_engine.auto_assign_peer_mentors(dept_id, conn)
        log_audit(user["id"], "AUTO_ASSIGN_MENTORS", f"Auto-assigned {count} peer mentors")
    flash(f"Auto-assigned {count} peer teacher(s).", "success" if count > 0 else "info")
    return redirect(url_for("admin.peer_mentors"))


@admin_bp.route("/peer-mentors/assign", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def assign_mentor():
    user = get_current_user()
    batch_id = request.form.get("batch_id", type=int)
    mentor_student_id = request.form.get("mentor_student_id", type=int)

    if not batch_id or not mentor_student_id:
        flash("Please select both a batch and a peer teacher.", "warning")
        return redirect(url_for("admin.peer_mentors"))

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO peer_mentors (mentor_student_id, batch_id, assigned_by, assigned_at)
            VALUES (?, ?, 'MANUAL', datetime('now'))
            ON CONFLICT(batch_id) DO UPDATE SET mentor_student_id = excluded.mentor_student_id,
                                                assigned_by = 'MANUAL',
                                                assigned_at = datetime('now')
            """,
            (mentor_student_id, batch_id),
        )
        conn.commit()
        log_audit(user["id"], "ASSIGN_MENTOR", f"Assigned mentor {mentor_student_id} to batch {batch_id}")
    flash("Peer teacher assigned successfully.", "success")
    return redirect(url_for("admin.peer_mentors"))


@admin_bp.route("/peer-mentors/remove/<int:mentor_id>", methods=["POST"])
@login_required(roles=["super_admin", "dept_admin"])
def remove_mentor(mentor_id):
    user = get_current_user()
    with get_db() as conn:
        conn.execute("DELETE FROM peer_mentors WHERE id = ?", (mentor_id,))
        conn.commit()
        log_audit(user["id"], "REMOVE_MENTOR", f"Removed peer mentor assignment {mentor_id}")
    flash("Peer teacher assignment removed.", "info")
    return redirect(url_for("admin.peer_mentors"))


@admin_bp.route("/extra-classes")
@login_required(roles=["super_admin", "dept_admin"])
def extra_classes():
    user = get_current_user()
    dept_id = _dept_scope(user)
    filter_subject = request.args.get("subject_id", type=int)
    filter_status = request.args.get("status", "").strip()

    with get_db() as conn:
        subjects = conn.execute(
            "SELECT id, name, code FROM subjects WHERE department_id = ? OR ? IS NULL ORDER BY name",
            (dept_id, dept_id),
        ).fetchall()

        query = """
            SELECT ecr.*, st.student_id AS student_id_text, st.full_name AS student_name,
                   s.name AS subject_name, s.code AS subject_code,
                   rb.batch_code, st_m.full_name AS peer_teacher_name
            FROM extra_class_requests ecr
            JOIN students st ON st.id = ecr.student_id
            JOIN subjects s ON s.id = ecr.subject_id
            LEFT JOIN remedial_batches rb ON rb.id = ecr.batch_id
            LEFT JOIN peer_mentors pm ON pm.batch_id = rb.id
            LEFT JOIN students st_m ON st_m.id = pm.mentor_student_id
            WHERE 1=1
        """
        params = []
        if dept_id:
            query += " AND st.department_id = ?"
            params.append(dept_id)
        if filter_subject:
            query += " AND ecr.subject_id = ?"
            params.append(filter_subject)
        if filter_status:
            query += " AND ecr.status = ?"
            params.append(filter_status)

        query += " ORDER BY ecr.requested_at DESC"
        enrollments = conn.execute(query, params).fetchall()

    return render_template(
        "admin/extra_classes.html",
        user=user,
        enrollments=enrollments,
        subjects=subjects,
        filter_subject=filter_subject,
        filter_status=filter_status,
    )


# ---------------------------------------------------------
# TEACHER / MENTOR PORTAL
# ---------------------------------------------------------

@teacher_bp.route("/dashboard")
@login_required(roles=["teacher"])
def dashboard():
    user = get_current_user()
    with get_db() as conn:
        teacher = conn.execute(
            "SELECT * FROM teachers WHERE user_id = ?", (user["id"],)
        ).fetchone()
        if not teacher:
            flash("Teacher profile not found.", "danger")
            return redirect(url_for("auth.logout"))

        batches = conn.execute(
            """
            SELECT rb.*, s.name AS subject_name, s.code AS subject_code,
                   COUNT(DISTINCT rs.student_id) AS student_count,
                   c.room_code,
                   (SELECT COUNT(*) FROM batch_attendance ba WHERE ba.batch_id = rb.id) AS total_attendance_entries
            FROM remedial_batches rb
            JOIN subjects s ON s.id = rb.subject_id
            LEFT JOIN classrooms c ON c.id = rb.classroom_id
            LEFT JOIN remedial_students rs ON rs.batch_id = rb.id
            WHERE rb.teacher_id = ?
            GROUP BY rb.id
            ORDER BY rb.status, rb.batch_code
            """,
            (teacher["id"],),
        ).fetchall()

    upcoming = ai_engine.get_upcoming_classes(teacher["id"], days=7)
    return render_template(
        "teacher/dashboard.html",
        user=user,
        teacher=teacher,
        batches=batches,
        upcoming=upcoming,
    )


@teacher_bp.route("/batch/<int:batch_id>", methods=["GET", "POST"])
@login_required(roles=["teacher"])
def batch_detail(batch_id):
    user = get_current_user()
    attendance_date = request.form.get("attendance_date") or request.args.get(
        "date", datetime.now().strftime("%Y-%m-%d")
    )
    with get_db() as conn:
        teacher = conn.execute(
            "SELECT * FROM teachers WHERE user_id = ?", (user["id"],)
        ).fetchone()
        if not teacher:
            flash("Teacher profile not found.", "danger")
            return redirect(url_for("auth.logout"))

        batch = conn.execute(
            """
            SELECT rb.*, s.name AS subject_name, s.code AS subject_code, c.room_code
            FROM remedial_batches rb
            JOIN subjects s ON s.id = rb.subject_id
            LEFT JOIN classrooms c ON c.id = rb.classroom_id
            WHERE rb.id = ? AND rb.teacher_id = ?
            """,
            (batch_id, teacher["id"]),
        ).fetchone()
        if not batch:
            flash("Batch not found or not assigned to you.", "danger")
            return redirect(url_for("teacher.dashboard"))

        students = conn.execute(
            """
            SELECT st.*
            FROM remedial_students rs
            JOIN students st ON st.id = rs.student_id
            WHERE rs.batch_id = ?
            ORDER BY st.full_name
            """,
            (batch_id,),
        ).fetchall()

        if request.method == "POST":
            # Find timetable_id if scheduled
            slot = conn.execute(
                "SELECT id FROM timetable WHERE batch_id = ? LIMIT 1", (batch_id,)
            ).fetchone()
            timetable_id = slot["id"] if slot else None

            marked_count = 0
            for student in students:
                raw_status = (request.form.get(f"att_{student['id']}") or request.form.get(f"status_{student['id']}") or "ABSENT").strip().upper()
                status = "PRESENT" if raw_status == "PRESENT" else "ABSENT"

                # Save to attendance table
                conn.execute(
                    """
                    INSERT INTO attendance
                    (timetable_id, batch_id, student_id, attendance_date, status, marked_by)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(batch_id, student_id, attendance_date)
                    DO UPDATE SET status = excluded.status,
                                  marked_by = excluded.marked_by,
                                  timetable_id = COALESCE(excluded.timetable_id, attendance.timetable_id)
                    """,
                    (timetable_id, batch_id, student["id"], attendance_date, status, user["id"]),
                )
                # Sync to batch_attendance table
                conn.execute(
                    """
                    INSERT INTO batch_attendance
                    (batch_id, student_id, attendance_date, status, marked_by)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(batch_id, student_id, attendance_date)
                    DO UPDATE SET status = excluded.status,
                                  marked_by = excluded.marked_by
                    """,
                    (batch_id, student["id"], attendance_date, status, user["id"]),
                )
                marked_count += 1

            remarks = request.form.get("remarks", "").strip()
            completion = request.form.get("completion_status", "IN_PROGRESS")
            if remarks or completion:
                conn.execute(
                    """
                    INSERT INTO teacher_reports (batch_id, teacher_id, remarks, completion_status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (batch_id, teacher["id"], remarks, completion),
                )
            if completion == "COMPLETED":
                conn.execute(
                    "UPDATE remedial_batches SET status = 'COMPLETED' WHERE id = ?",
                    (batch_id,),
                )
            conn.commit()
            log_audit(user["id"], "MARK_ATTENDANCE", f"Marked attendance for {batch['batch_code']} ({marked_count} students) on {attendance_date}")
            flash(f"Attendance for {marked_count} student(s) successfully saved for {batch['batch_code']} on {attendance_date}.", "success")
            return redirect(url_for("teacher.batch_detail", batch_id=batch_id, date=attendance_date))

        # Query attendance on selected date
        rows = conn.execute(
            """
            SELECT student_id, status FROM attendance
            WHERE batch_id = ? AND attendance_date = ?
            UNION
            SELECT student_id, status FROM batch_attendance
            WHERE batch_id = ? AND attendance_date = ?
            """,
            (batch_id, attendance_date, batch_id, attendance_date),
        ).fetchall()
        attendance_map = {r["student_id"]: r["status"] for r in rows}

        # Query latest report
        latest_report = conn.execute(
            """
            SELECT * FROM teacher_reports
            WHERE batch_id = ? AND teacher_id = ?
            ORDER BY submitted_at DESC
            LIMIT 1
            """,
            (batch_id, teacher["id"]),
        ).fetchone()

        # Query schedule slots
        routine_slots = conn.execute(
            """
            SELECT tt.*, c.room_code
            FROM timetable tt
            JOIN classrooms c ON c.id = tt.classroom_id
            WHERE tt.batch_id = ?
            ORDER BY tt.day_of_week, tt.start_time
            """,
            (batch_id,),
        ).fetchall()

    return render_template(
        "teacher/batch_detail.html",
        user=user,
        batch=batch,
        students=students,
        attendance_date=attendance_date,
        attendance_map=attendance_map,
        latest_report=latest_report,
        routine_slots=routine_slots,
    )


@teacher_bp.route("/extra-class-attendance/<int:batch_id>", methods=["GET", "POST"])
@login_required(roles=["teacher"])
def extra_class_attendance(batch_id):
    """Extra class attendance checklist for teachers to mark daily attendance."""
    user = get_current_user()
    attendance_date = request.form.get("attendance_date") or request.args.get(
        "date", datetime.now().strftime("%Y-%m-%d")
    )
    with get_db() as conn:
        teacher = conn.execute(
            "SELECT * FROM teachers WHERE user_id = ?", (user["id"],)
        ).fetchone()
        if not teacher:
            flash("Teacher profile not found.", "danger")
            return redirect(url_for("auth.logout"))

        batch = conn.execute(
            """
            SELECT rb.*, s.name AS subject_name, s.code AS subject_code, c.room_code
            FROM remedial_batches rb
            JOIN subjects s ON s.id = rb.subject_id
            LEFT JOIN classrooms c ON c.id = rb.classroom_id
            WHERE rb.id = ? AND rb.teacher_id = ?
            """,
            (batch_id, teacher["id"]),
        ).fetchone()
        if not batch:
            flash("Batch not found or not assigned to you.", "danger")
            return redirect(url_for("teacher.dashboard"))

        students = conn.execute(
            """
            SELECT st.*
            FROM remedial_students rs
            JOIN students st ON st.id = rs.student_id
            WHERE rs.batch_id = ?
            ORDER BY st.full_name
            """,
            (batch_id,),
        ).fetchall()

        if request.method == "POST":
            marked_count = 0
            for student in students:
                raw_status = (request.form.get(f"att_{student['id']}") or "ABSENT").strip().upper()
                status = "PRESENT" if raw_status == "PRESENT" else "ABSENT"
                remarks = request.form.get(f"remarks_{student['id']}", "").strip()

                conn.execute(
                    """
                    INSERT INTO extra_class_attendance
                    (batch_id, student_id, attendance_date, status, marked_by, remarks)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(batch_id, student_id, attendance_date)
                    DO UPDATE SET status = excluded.status,
                                  marked_by = excluded.marked_by,
                                  remarks = excluded.remarks
                    """,
                    (batch_id, student["id"], attendance_date, status, user["id"], remarks),
                )
                marked_count += 1

            conn.commit()
            log_audit(user["id"], "MARK_EXTRA_CLASS_ATTENDANCE", f"Marked extra class attendance for {batch['batch_code']} ({marked_count} students) on {attendance_date}")
            flash(f"Extra class attendance for {marked_count} student(s) successfully saved for {batch['batch_code']} on {attendance_date}.", "success")
            return redirect(url_for("teacher.extra_class_attendance", batch_id=batch_id, date=attendance_date))

        # Query attendance on selected date
        rows = conn.execute(
            """
            SELECT student_id, status, remarks FROM extra_class_attendance
            WHERE batch_id = ? AND attendance_date = ?
            """,
            (batch_id, attendance_date),
        ).fetchall()
        attendance_map = {r["student_id"]: {"status": r["status"], "remarks": r.get("remarks")} for r in rows}

    return render_template(
        "teacher/extra_class_attendance.html",
        user=user,
        batch=batch,
        students=students,
        attendance_date=attendance_date,
        attendance_map=attendance_map,
    )


# ---------------------------------------------------------
# STUDENT PORTAL
# ---------------------------------------------------------

def _student_profile(conn, user):
    student = conn.execute(
        "SELECT * FROM students WHERE user_id = ?", (user["id"],)
    ).fetchone()
    if student:
        return student
    return conn.execute(
        "SELECT * FROM students WHERE UPPER(student_id) = UPPER(?)",
        (user["username"],),
    ).fetchone()


def _student_failures(conn, student):
    return conn.execute(
        """
        SELECT r.*, s.name AS subject_name, s.code AS subject_code
        FROM results r
        JOIN subjects s ON s.id = r.subject_id
        WHERE r.student_id = ? AND r.result = 'FAIL'
        ORDER BY r.uploaded_at DESC
        """,
        (student["student_id"],),
    ).fetchall()


def _student_schedule(conn, student):
    return conn.execute(
        """
        SELECT tt.*, rb.batch_code, s.name AS subject_name, s.code AS subject_code,
               c.room_code, te.full_name AS teacher_name
        FROM remedial_students rs
        JOIN remedial_batches rb ON rb.id = rs.batch_id
        JOIN timetable tt ON tt.batch_id = rb.id
        JOIN subjects s ON s.id = rb.subject_id
        JOIN classrooms c ON c.id = tt.classroom_id
        JOIN teachers te ON te.id = tt.teacher_id
        WHERE rs.student_id = ?
        ORDER BY CASE tt.day_of_week WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3 WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6 ELSE 7 END, tt.start_time
        """,
        (student["id"],),
    ).fetchall()


def _student_attendance_rows(conn, student):
    return conn.execute(
        """
        SELECT a.attendance_date, a.status, s.name AS subject_name, s.code AS subject_code,
               rb.batch_code, u.full_name AS mentor_name
        FROM (
            SELECT batch_id, student_id, attendance_date, status, marked_by FROM attendance WHERE student_id = ?
            UNION
            SELECT batch_id, student_id, attendance_date, status, marked_by FROM batch_attendance WHERE student_id = ?
        ) a
        JOIN remedial_batches rb ON rb.id = a.batch_id
        JOIN subjects s ON s.id = rb.subject_id
        LEFT JOIN users u ON u.id = a.marked_by
        ORDER BY a.attendance_date DESC
        """,
        (student["id"], student["id"]),
    ).fetchall()


def _student_attendance_summary(conn, student):
    mentor_by_subject = conn.execute(
        """
        SELECT s.id AS subject_id, s.name AS subject_name, s.code AS subject_code,
               SUM(CASE WHEN a.status = 'PRESENT' THEN 1 ELSE 0 END) AS present_count,
               SUM(CASE WHEN a.status = 'ABSENT' THEN 1 ELSE 0 END) AS absent_count,
               COUNT(*) AS total_count
        FROM (
            SELECT batch_id, student_id, attendance_date, status FROM attendance WHERE student_id = ?
            UNION
            SELECT batch_id, student_id, attendance_date, status FROM batch_attendance WHERE student_id = ?
        ) a
        JOIN remedial_batches rb ON rb.id = a.batch_id
        JOIN subjects s ON s.id = rb.subject_id
        GROUP BY s.id, s.name, s.code
        ORDER BY s.name
        """,
        (student["id"], student["id"]),
    ).fetchall()

    semester_rows = conn.execute(
        """
        SELECT sa.subject_id,
               COALESCE(s.name, 'Overall') AS subject_name,
               COALESCE(s.code, 'OVERALL') AS subject_code,
               sa.classes_attended AS present_count,
               CASE
                   WHEN sa.total_classes > sa.classes_attended
                   THEN sa.total_classes - sa.classes_attended
                   ELSE 0
               END AS absent_count,
               sa.total_classes AS total_count,
               sa.attendance_percent AS percent
        FROM semester_attendance sa
        LEFT JOIN subjects s ON s.id = sa.subject_id
        WHERE sa.student_id = ? AND sa.semester = ?
        ORDER BY s.name
        """,
        (student["id"], student["semester"]),
    ).fetchall()

    subject_map = {}
    for r in semester_rows:
        sid = r["subject_id"] or 0
        subject_map[sid] = {
            "subject_name": r["subject_name"],
            "subject_code": r["subject_code"],
            "present_count": r["present_count"] or 0,
            "absent_count": r["absent_count"] or 0,
            "total_count": r["total_count"] or 0,
        }

    for r in mentor_by_subject:
        sid = r["subject_id"]
        if sid in subject_map:
            subject_map[sid]["present_count"] += (r["present_count"] or 0)
            subject_map[sid]["absent_count"] += (r["absent_count"] or 0)
            subject_map[sid]["total_count"] += (r["total_count"] or 0)
        else:
            subject_map[sid] = {
                "subject_name": r["subject_name"],
                "subject_code": r["subject_code"],
                "present_count": r["present_count"] or 0,
                "absent_count": r["absent_count"] or 0,
                "total_count": r["total_count"] or 0,
            }

    summary = []
    total_present = 0
    total_classes = 0
    for sid, info in sorted(subject_map.items(), key=lambda x: x[1]["subject_name"]):
        tot = info["total_count"]
        pres = info["present_count"]
        pct = round((pres / tot) * 100) if tot > 0 else 0
        summary.append({
            "subject_name": info["subject_name"],
            "subject_code": info["subject_code"],
            "present_count": pres,
            "absent_count": info["absent_count"],
            "total_count": tot,
            "percent": pct,
        })
        total_present += pres
        total_classes += tot

    overall = round((total_present / total_classes) * 100) if total_classes > 0 else 0
    return summary, overall, total_present, total_classes


@student_bp.route("/dashboard")
@login_required(roles=["student"])
def dashboard():
    user = get_current_user()
    with get_db() as conn:
        student = _student_profile(conn, user)
        if not student:
            flash("Student profile not found.", "danger")
            return redirect(url_for("auth.logout"))

        failures = _student_failures(conn, student)
        schedule = _student_schedule(conn, student)
        attendance_summary, overall_pct, present_count, total_classes = _student_attendance_summary(
            conn, student
        )
        attendance = _student_attendance_rows(conn, student)[:30]

        feedback_batches = conn.execute(
            """
            SELECT rb.id, rb.batch_code, s.name AS subject_name,
                   sf.rating, sf.comments
            FROM remedial_students rs
            JOIN remedial_batches rb ON rb.id = rs.batch_id
            JOIN subjects s ON s.id = rb.subject_id
            LEFT JOIN student_feedback sf
                ON sf.batch_id = rb.id AND sf.student_id = rs.student_id
            WHERE rs.student_id = ?
            ORDER BY rb.batch_code
            """,
            (student["id"],),
        ).fetchall()

        # Weak student detection using central ai_engine logic and dynamic threshold (default 75%)
        min_threshold = float(get_setting("min_attendance_threshold", 75, conn=conn))
        dept_weak_all = ai_engine.identify_weak_students(student["department_id"], min_attendance_threshold=min_threshold, conn=conn)
        my_eval = next((st for st in dept_weak_all if st["id"] == student["id"]), None)
        weakness_reasons = []
        is_weak = False
        if my_eval and my_eval.get("reason_for_selection") not in ("None", None):
            is_weak = True
            if my_eval.get("has_backlogs"):
                weakness_reasons.append(f"{my_eval['total_backlogs']} backlog subject(s): {my_eval['failed_subjects']}")
            if my_eval.get("has_low_attendance"):
                weakness_reasons.append(f"Attendance ({my_eval['attendance_percentage']}) is below {min_threshold:.1f}% threshold")

        # Assigned peer mentor
        peer_mentor = conn.execute(
            """
            SELECT pm.*, st.student_id AS mentor_id, st.full_name AS mentor_name,
                   s.name AS subject_name, rb.batch_code
            FROM remedial_students rs
            JOIN peer_mentors pm ON pm.batch_id = rs.batch_id
            JOIN students st ON st.id = pm.mentor_student_id
            JOIN remedial_batches rb ON rb.id = rs.batch_id
            JOIN subjects s ON s.id = rb.subject_id
            WHERE rs.student_id = ?
            LIMIT 1
            """,
            (student["id"],),
        ).fetchone()

    return render_template(
        "student/dashboard.html",
        user=user,
        student=student,
        failures=failures,
        schedule=schedule,
        attendance=attendance,
        attendance_summary=attendance_summary,
        overall_attendance=overall_pct,
        present_count=present_count,
        total_classes=total_classes,
        feedback_batches=feedback_batches,
        is_weak=is_weak,
        weakness_reasons=weakness_reasons,
        peer_mentor=peer_mentor,
        low_threshold=min_threshold,
    )


@student_bp.route("/attendance")
@login_required(roles=["student"])
def attendance_dashboard():
    user = get_current_user()
    with get_db() as conn:
        student = _student_profile(conn, user)
        if not student:
            flash("Student profile not found.", "danger")
            return redirect(url_for("auth.logout"))
        low_threshold = float(get_setting("min_attendance_threshold", 75, conn=conn))
        summary, overall_pct, present_count, total_classes = _student_attendance_summary(
            conn, student
        )
        records = _student_attendance_rows(conn, student)
    return render_template(
        "student/attendance.html",
        user=user,
        student=student,
        attendance_summary=summary,
        overall_attendance=overall_pct,
        present_count=present_count,
        total_classes=total_classes,
        attendance=records,
        low_threshold=low_threshold,
    )


@student_bp.route("/routine")
@login_required(roles=["student"])
def routine():
    user = get_current_user()
    with get_db() as conn:
        student = _student_profile(conn, user)
        if not student:
            flash("Student profile not found.", "danger")
            return redirect(url_for("auth.logout"))
        schedule = _student_schedule(conn, student)
    return render_template(
        "student/routine.html",
        user=user,
        student=student,
        schedule=schedule,
    )


@student_bp.route("/feedback/<int:batch_id>", methods=["POST"])
@login_required(roles=["student"])
def submit_feedback(batch_id):
    user = get_current_user()
    rating = int(request.form.get("rating", 0))
    comments = request.form.get("comments", "").strip()
    if rating < 1 or rating > 5:
        flash("Please select a rating between 1 and 5.", "warning")
        return redirect(url_for("student.dashboard"))

    with get_db() as conn:
        student = _student_profile(conn, user)
        if not student:
            flash("Student record not found.", "danger")
            return redirect(url_for("auth.logout"))

        enrolled = conn.execute(
            "SELECT 1 FROM remedial_students WHERE batch_id = ? AND student_id = ?",
            (batch_id, student["id"]),
        ).fetchone()
        if not enrolled:
            flash("You are not enrolled in this batch.", "danger")
            return redirect(url_for("student.dashboard"))

        conn.execute(
            """
            INSERT INTO student_feedback (student_id, batch_id, rating, comments)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(student_id, batch_id)
            DO UPDATE SET rating = excluded.rating, comments = excluded.comments,
                          submitted_at = datetime('now')
            """,
            (student["id"], batch_id, rating, comments),
        )
        conn.commit()
    flash("Thank you for submitting your feedback.", "success")
    return redirect(url_for("student.dashboard"))


@student_bp.route("/extra-classes")
@login_required(roles=["student"])
def extra_classes():
    user = get_current_user()
    with get_db() as conn:
        student = _student_profile(conn, user)
        if not student:
            flash("Student profile not found.", "danger")
            return redirect(url_for("auth.logout"))

        enrolled_query = """
            SELECT ecr.*, s.name AS subject_name, s.code AS subject_code,
                   rb.batch_code,
                   st_m.full_name AS mentor_name
            FROM extra_class_requests ecr
            JOIN subjects s ON s.id = ecr.subject_id
            LEFT JOIN remedial_batches rb ON rb.id = ecr.batch_id
            LEFT JOIN peer_mentors pm ON pm.batch_id = rb.id
            LEFT JOIN students st_m ON st_m.id = pm.mentor_student_id
            WHERE ecr.student_id = ? AND ecr.status = 'ENROLLED'
            ORDER BY ecr.requested_at DESC
        """
        enrolled_subjects = conn.execute(enrolled_query, (student["id"],)).fetchall()

        available_query = """
            SELECT rb.id AS batch_id, rb.batch_code, rb.semester,
                   s.id AS subject_id, s.name AS subject_name, s.code AS subject_code,
                   st_m.full_name AS mentor_name
            FROM remedial_batches rb
            JOIN subjects s ON s.id = rb.subject_id
            LEFT JOIN peer_mentors pm ON pm.batch_id = rb.id
            LEFT JOIN students st_m ON st_m.id = pm.mentor_student_id
            WHERE rb.department_id = ? AND rb.semester = ?
              AND rb.id NOT IN (
                  SELECT batch_id FROM extra_class_requests
                  WHERE student_id = ? AND status = 'ENROLLED' AND batch_id IS NOT NULL
              )
            ORDER BY s.name
        """
        available_subjects = conn.execute(
            available_query,
            (student["department_id"], student["semester"], student["id"]),
        ).fetchall()

        if not available_subjects:
            available_subjects = conn.execute(
                """
                SELECT NULL AS batch_id, NULL AS batch_code, ? AS semester,
                       s.id AS subject_id, s.name AS subject_name, s.code AS subject_code,
                       NULL AS mentor_name
                FROM subjects s
                WHERE s.department_id = ?
                  AND s.id NOT IN (
                      SELECT subject_id FROM extra_class_requests
                      WHERE student_id = ? AND status = 'ENROLLED'
                  )
                ORDER BY s.name
                """,
                (student["semester"], student["department_id"], student["id"]),
            ).fetchall()

    return render_template(
        "student/extra_classes.html",
        user=user,
        student=student,
        enrolled_subjects=enrolled_subjects,
        available_subjects=available_subjects,
    )


@student_bp.route("/extra-classes/enroll", methods=["POST"])
@login_required(roles=["student"])
def enroll_extra_class():
    user = get_current_user()
    subject_id = request.form.get("subject_id", type=int)
    batch_id_raw = request.form.get("batch_id")
    batch_id = int(batch_id_raw) if batch_id_raw and batch_id_raw.isdigit() else None

    if not subject_id:
        flash("Invalid subject selected.", "warning")
        return redirect(url_for("student.extra_classes"))

    with get_db() as conn:
        student = _student_profile(conn, user)
        if not student:
            flash("Student profile not found.", "danger")
            return redirect(url_for("auth.logout"))

        conn.execute(
            """
            INSERT INTO extra_class_requests (student_id, subject_id, batch_id, status, requested_at)
            VALUES (?, ?, ?, 'ENROLLED', datetime('now'))
            ON CONFLICT(student_id, subject_id, batch_id)
            DO UPDATE SET status = 'ENROLLED', requested_at = datetime('now')
            """,
            (student["id"], subject_id, batch_id),
        )

        if batch_id:
            conn.execute(
                """
                INSERT OR IGNORE INTO remedial_students (batch_id, student_id)
                VALUES (?, ?)
                """,
                (batch_id, student["id"]),
            )

        conn.commit()
        log_audit(user["id"], "ENROLL_EXTRA_CLASS", f"Enrolled in extra class for subject {subject_id}")

    flash("You have successfully enrolled in the after-college extra class (3:30 PM - 4:30 PM)!", "success")
    return redirect(url_for("student.extra_classes"))


@student_bp.route("/extra-classes/withdraw", methods=["POST"])
@login_required(roles=["student"])
def withdraw_extra_class():
    user = get_current_user()
    request_id = request.form.get("request_id", type=int)

    if not request_id:
        flash("Invalid request.", "warning")
        return redirect(url_for("student.extra_classes"))

    with get_db() as conn:
        student = _student_profile(conn, user)
        if not student:
            flash("Student profile not found.", "danger")
            return redirect(url_for("auth.logout"))

        conn.execute(
            """
            UPDATE extra_class_requests
            SET status = 'WITHDRAWN'
            WHERE id = ? AND student_id = ?
            """,
            (request_id, student["id"]),
        )
        conn.commit()
        log_audit(user["id"], "WITHDRAW_EXTRA_CLASS", f"Withdrew from extra class request {request_id}")

    flash("You have withdrawn from the extra class.", "info")
    return redirect(url_for("student.extra_classes"))

