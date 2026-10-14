from datetime import timedelta
from flask import Flask, render_template, session

from auth import get_current_user
from db import get_db, init_db
from routes import admin_bp, auth_bp, student_bp, teacher_bp


def create_app():
    app = Flask(__name__, static_folder='static', static_url_path='/static')
    app.config["SECRET_KEY"] = "major-project-1c-remedial-secret-key"
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    # Session configuration to prevent session expiration during file uploads
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=24)
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True

    @app.context_processor
    def inject_user():
        user = get_current_user()
        unread_count = 0
        if user:
            try:
                with get_db() as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND is_read = 0",
                        (user["id"],),
                    ).fetchone()
                    unread_count = row["c"] if row else 0
            except Exception:
                unread_count = 0
        return {"user": user, "unread_notifications_count": unread_count}

    init_db()

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(student_bp)

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="Page not found"), 404

    @app.errorhandler(500)
    def server_error(_error):
        return render_template("error.html", code=500, message="Internal server error"), 500

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
