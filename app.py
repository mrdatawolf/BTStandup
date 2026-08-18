import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def get_database_path() -> Path:
    return resolve_path(os.getenv("DATABASE_PATH", "./data/standup.db"))


def connect_database(database_path: Path | None = None) -> sqlite3.Connection:
    path = database_path or get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database(database_path: Path | None = None) -> None:
    with connect_database(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 500),
                progress INTEGER NOT NULL DEFAULT 0
                    CHECK(progress BETWEEN 0 AND 100),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def entry_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "progress": row["progress"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_app(database_path: Path | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["DATABASE_PATH"] = database_path or get_database_path()
    initialize_database(app.config["DATABASE_PATH"])

    def database() -> sqlite3.Connection:
        return connect_database(app.config["DATABASE_PATH"])

    @app.get("/")
    def index():
        return send_from_directory(BASE_DIR, "standup.html")

    @app.get("/api/health")
    def health():
        with database() as connection:
            connection.execute("SELECT 1").fetchone()
        return jsonify({"status": "ok"})

    @app.get("/api/entries")
    def list_entries():
        with database() as connection:
            rows = connection.execute(
                "SELECT id, name, progress, created_at, updated_at "
                "FROM entries ORDER BY id"
            ).fetchall()
        return jsonify([entry_to_dict(row) for row in rows])

    @app.post("/api/entries")
    def add_entry():
        payload = request.get_json(silent=True) or {}
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            return jsonify({"error": "Name is required."}), 400
        name = name.strip()
        if len(name) > 500:
            return jsonify({"error": "Name must be 500 characters or fewer."}), 400

        with database() as connection:
            cursor = connection.execute(
                "INSERT INTO entries (name, progress) VALUES (?, 0)", (name,)
            )
            row = connection.execute(
                "SELECT id, name, progress, created_at, updated_at "
                "FROM entries WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return jsonify(entry_to_dict(row)), 201

    @app.patch("/api/entries/<int:entry_id>")
    def update_entry(entry_id: int):
        payload = request.get_json(silent=True) or {}
        progress = payload.get("progress")
        if isinstance(progress, bool) or not isinstance(progress, int):
            return jsonify({"error": "Progress must be a whole number."}), 400
        if not 0 <= progress <= 100:
            return jsonify({"error": "Progress must be between 0 and 100."}), 400

        with database() as connection:
            cursor = connection.execute(
                "UPDATE entries SET progress = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (progress, entry_id),
            )
            if cursor.rowcount == 0:
                return jsonify({"error": "Entry not found."}), 404
            row = connection.execute(
                "SELECT id, name, progress, created_at, updated_at "
                "FROM entries WHERE id = ?",
                (entry_id,),
            ).fetchone()
        return jsonify(entry_to_dict(row))

    @app.delete("/api/entries/<int:entry_id>")
    def delete_entry(entry_id: int):
        with database() as connection:
            cursor = connection.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            if cursor.rowcount == 0:
                return jsonify({"error": "Entry not found."}), 404
        return "", 204

    return app


if __name__ == "__main__":
    from waitress import serve

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    print(f"Starting BT Standup at http://{host}:{port}")
    print(f"Database: {get_database_path()}")
    serve(create_app(), host=host, port=port)
