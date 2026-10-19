from datetime import timedelta
from flask import Flask, render_template, session
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from pathlib import Path

from auth import get_current_user
from db import get_db, init_db
from routes import admin_bp, auth_bp, student_bp, teacher_bp

# Load environment variables from .env file ONLY if it exists (for local development)
# This allows using Windows User Environment Variables or Render env vars without requiring .env
env_path = Path(__file__).resolve().parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)


def create_app():
    app = Flask(__name__, static_folder='static', static_url_path='/static')
    app.config["SECRET_KEY"] = "major-project-1c-remedial-secret-key"
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    # Fix URL generation behind Render's proxy (HTTPS, correct host)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

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
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
