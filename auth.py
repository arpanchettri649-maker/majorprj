import os
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps

from flask import flash, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password_hash, password):
    return check_password_hash(password_hash, password)


def _fetch_user(conn, user_id=None, username=None, active_only=True):
    active_sql = " AND u.is_active = 1" if active_only else ""
    if user_id is not None:
        return conn.execute(
            f"""
            SELECT u.*, d.code AS dept_code, d.name AS dept_name
            FROM users u
            LEFT JOIN departments d ON d.id = u.department_id
            WHERE u.id = ?{active_sql}
            """,
            (user_id,),
        ).fetchone()
    return conn.execute(
        f"""
        SELECT u.*, d.code AS dept_code, d.name AS dept_name
        FROM users u
        LEFT JOIN departments d ON d.id = u.department_id
        WHERE u.username = ?{active_sql}
        """,
        (username,),
    ).fetchone()


def _login_candidates(conn, username, active_only=True):
    candidates = []
    seen = set()

    def add_candidate(row):
        if row and row["id"] not in seen:
            seen.add(row["id"])
            candidates.append(row)

    add_candidate(_fetch_user(conn, username=username, active_only=active_only))
    active_sql = " AND u.is_active = 1" if active_only else ""
    for row in conn.execute(
        f"""
        SELECT u.*, d.code AS dept_code, d.name AS dept_name
        FROM users u
        LEFT JOIN departments d ON d.id = u.department_id
        WHERE LOWER(u.username) = LOWER(?){active_sql}
        """,
        (username,),
    ).fetchall():
        add_candidate(row)
    for student in conn.execute(
        """
        SELECT user_id FROM students
        WHERE UPPER(student_id) = UPPER(?) AND user_id IS NOT NULL
        """,
        (username,),
    ).fetchall():
        add_candidate(_fetch_user(conn, user_id=student["user_id"], active_only=active_only))
    return candidates


def record_failed_login(username, ip_address=None, user_agent=None):
    """Record a failed login attempt for administrator review."""
    username = (username or "").strip()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO failed_logins (username, ip_address, user_agent)
            VALUES (?, ?, ?)
            """,
            (username, ip_address, user_agent),
        )


def get_failed_logins(limit=100):
    """Retrieve recent failed login attempts for admin review."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM failed_logins
            ORDER BY attempted_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def login_user(username, password):
    username = (username or "").strip()
    with get_db() as conn:
        for user in _login_candidates(conn, username, active_only=True):
            if verify_password(user["password_hash"], password):
                return dict(user)
        return None


def login_status(username, password):
    """Return (user_dict, status) where status is ok, pending, rejected, or invalid."""
    username = (username or "").strip()
    with get_db() as conn:
        matched = None
        for user in _login_candidates(conn, username, active_only=False):
            if verify_password(user["password_hash"], password):
                matched = dict(user)
                break
        if not matched:
            return None, "invalid"
        if matched.get("is_active"):
            return matched, "ok"
        student = conn.execute(
            """
            SELECT registration_status FROM students
            WHERE user_id = ? OR UPPER(student_id) = UPPER(?)
            """,
            (matched["id"], username),
        ).fetchone()
        status = (student["registration_status"] if student else "") or "PENDING"
        if status == "REJECTED":
            return matched, "rejected"
        return matched, "pending"


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with get_db() as conn:
        user = conn.execute(
            """
            SELECT u.*, d.code AS dept_code, d.name AS dept_name
            FROM users u
            LEFT JOIN departments d ON d.id = u.department_id
            WHERE u.id = ? AND u.is_active = 1
            """,
            (user_id,),
        ).fetchone()
        return dict(user) if user else None


def login_required(roles=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if not user:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("auth.login"))
            if roles and user["role"] not in roles:
                flash("You do not have permission to access that page.", "danger")
                return redirect(url_for("auth.index"))
            return view(*args, **kwargs)

        return wrapped

    return decorator


def send_email(to_email, subject, body, html_body=None):
    """Send email using Gmail SMTP with STARTTLS (port 587).
    
    Uses port 587 with STARTTLS instead of port 465 with implicit SSL
    because many networks/firewalls block implicit SSL connections.
    """
    email_address = os.environ.get("EMAIL_ADDRESS")
    email_password = os.environ.get("EMAIL_APP_PASSWORD")
    
    if not email_address or not email_password:
        print(f"ERROR: EMAIL_ADDRESS or EMAIL_APP_PASSWORD environment variables not set")
        print(f"Would send email to: {to_email}")
        print(f"Subject: {subject}")
        print(f"Body: {body}")
        return False
    
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = email_address
        msg["To"] = to_email
        
        # Plain text version
        text_part = MIMEText(body, "plain")
        msg.attach(text_part)
        
        # HTML version (if provided)
        if html_body:
            html_part = MIMEText(html_body, "html")
            msg.attach(html_part)
        
        # Connect to Gmail SMTP with STARTTLS (port 587)
        # This works better on networks that block implicit SSL (port 465)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(email_address, email_password)
            server.send_message(msg)
            server.quit()
        
        print(f"Email sent successfully to {to_email}")
        return True
    except Exception as e:
        print(f"ERROR sending email: {str(e)}")
        return False


def generate_reset_token(user_id, expiry_minutes=15):
    """Generate a secure password reset token (expires in 15 minutes)."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now() + timedelta(minutes=expiry_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO password_reset_tokens (user_id, token, expires_at)
            VALUES (?, ?, ?)
            """,
            (user_id, token, expires_at),
        )
        conn.commit()
    return token


def validate_reset_token(token):
    """Validate a password reset token and return user_id if valid."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT user_id, expires_at, used_at FROM password_reset_tokens
            WHERE token = ? AND used_at IS NULL
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        # Check if token is expired
        expires_at = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
        if datetime.now() > expires_at:
            return None
        return row["user_id"]


def mark_token_used(token, conn=None):
    """Mark a reset token as used."""
    if conn is not None:
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = datetime('now') WHERE token = ?",
            (token,),
        )
    else:
        with get_db() as local_conn:
            local_conn.execute(
                "UPDATE password_reset_tokens SET used_at = datetime('now') WHERE token = ?",
                (token,),
            )
            local_conn.commit()


def cleanup_expired_tokens():
    """Clean up expired and used reset tokens."""
    with get_db() as conn:
        conn.execute(
            """
            DELETE FROM password_reset_tokens
            WHERE expires_at < datetime('now') OR used_at IS NOT NULL
            """
        )
        conn.commit()
