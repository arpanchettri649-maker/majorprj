from functools import wraps

from flask import flash, redirect, session, url_for
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
