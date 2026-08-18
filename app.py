import os
import re
import sqlite3
from datetime import date
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
        existing_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(entries)")
        }
        migrations = {
            "date": "ALTER TABLE entries ADD COLUMN date TEXT NOT NULL DEFAULT ''",
            "initials": "ALTER TABLE entries ADD COLUMN initials TEXT NOT NULL DEFAULT ''",
            "notes": "ALTER TABLE entries ADD COLUMN notes TEXT NOT NULL DEFAULT ''",
            "sort_order": "ALTER TABLE entries ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0",
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                connection.execute(statement)
        connection.execute(
            "UPDATE entries SET sort_order = id WHERE sort_order = 0"
        )


def entry_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "progress": row["progress"],
        "date": row["date"],
        "initials": row["initials"],
        "notes": row["notes"],
        "sort_order": row["sort_order"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_app(database_path: Path | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["DATABASE_PATH"] = database_path or get_database_path()
    initialize_database(app.config["DATABASE_PATH"])

    def database() -> sqlite3.Connection:
        return connect_database(app.config["DATABASE_PATH"])

    def validate_fields(payload: dict, creating: bool = False):
        allowed = {"name", "progress", "date", "initials", "notes"}
        if creating and "name" not in payload:
            return None, "Name is required."
        if not any(field in payload for field in allowed):
            return None, "No supported fields were provided."

        values = {}
        if "name" in payload:
            name = payload["name"]
            if not isinstance(name, str) or not name.strip():
                return None, "Name is required."
            name = name.strip()
            if len(name) > 500:
                return None, "Name must be 500 characters or fewer."
            values["name"] = name

        if "progress" in payload:
            progress = payload["progress"]
            if isinstance(progress, bool) or not isinstance(progress, int):
                return None, "Progress must be a whole number."
            if not 0 <= progress <= 100:
                return None, "Progress must be between 0 and 100."
            values["progress"] = progress

        if "date" in payload:
            date_value = payload["date"]
            if not isinstance(date_value, str):
                return None, "Date must be in YYYY-MM-DD format."
            if date_value:
                try:
                    date.fromisoformat(date_value)
                except ValueError:
                    return None, "Date must be a valid date in YYYY-MM-DD format."
            values["date"] = date_value

        if "initials" in payload:
            initials = payload["initials"]
            if not isinstance(initials, str) or not re.fullmatch(r"[A-Z0-9]{0,5}", initials):
                return None, "Initials must contain up to 5 uppercase letters or numbers."
            values["initials"] = initials

        if "notes" in payload:
            notes = payload["notes"]
            if not isinstance(notes, str) or len(notes) > 10_000:
                return None, "Notes must be 10,000 characters or fewer."
            values["notes"] = notes

        return values, None

    @app.get("/")
    def index():
        return send_from_directory(BASE_DIR, "standup.html")

    @app.get("/app.js")
    def browser_script():
        return send_from_directory(BASE_DIR, "app.js")

    @app.get("/api/health")
    def health():
        with database() as connection:
            connection.execute("SELECT 1").fetchone()
        return jsonify({"status": "ok"})

    @app.get("/api/version")
    def version():
        version_file = BASE_DIR / "VERSION.txt"
        value = version_file.read_text(encoding="utf-8").strip()
        if value.lower().startswith("version="):
            value = value.split("=", 1)[1].strip()
        return jsonify({"version": value})

    @app.get("/api/entries")
    def list_entries():
        with database() as connection:
            rows = connection.execute(
                "SELECT id, name, progress, date, initials, notes, sort_order, "
                "created_at, updated_at FROM entries ORDER BY sort_order, id"
            ).fetchall()
        return jsonify([entry_to_dict(row) for row in rows])

    @app.post("/api/entries")
    def add_entry():
        payload = request.get_json(silent=True) or {}
        values, error = validate_fields(payload, creating=True)
        if error:
            return jsonify({"error": error}), 400

        with database() as connection:
            next_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM entries"
            ).fetchone()[0]
            cursor = connection.execute(
                "INSERT INTO entries (name, progress, date, initials, notes, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    values["name"], values.get("progress", 0), values.get("date", ""),
                    values.get("initials", ""), values.get("notes", ""), next_order,
                ),
            )
            row = connection.execute(
                "SELECT id, name, progress, date, initials, notes, sort_order, "
                "created_at, updated_at "
                "FROM entries WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return jsonify(entry_to_dict(row)), 201

    @app.patch("/api/entries/<int:entry_id>")
    def update_entry(entry_id: int):
        payload = request.get_json(silent=True) or {}
        values, error = validate_fields(payload)
        if error:
            return jsonify({"error": error}), 400

        with database() as connection:
            assignments = ", ".join(f"{field} = ?" for field in values)
            cursor = connection.execute(
                f"UPDATE entries SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (*values.values(), entry_id),
            )
            if cursor.rowcount == 0:
                return jsonify({"error": "Entry not found."}), 404
            row = connection.execute(
                "SELECT id, name, progress, date, initials, notes, sort_order, "
                "created_at, updated_at "
                "FROM entries WHERE id = ?",
                (entry_id,),
            ).fetchone()
        return jsonify(entry_to_dict(row))

    @app.put("/api/entries/order")
    def reorder_entries():
        payload = request.get_json(silent=True) or {}
        entry_ids = payload.get("entry_ids")
        if (
            not isinstance(entry_ids, list)
            or any(isinstance(item, bool) or not isinstance(item, int) for item in entry_ids)
            or len(entry_ids) != len(set(entry_ids))
        ):
            return jsonify({"error": "entry_ids must be a unique list of entry IDs."}), 400

        with database() as connection:
            current_ids = {row[0] for row in connection.execute("SELECT id FROM entries")}
            if set(entry_ids) != current_ids:
                return jsonify({"error": "The order must include every entry exactly once."}), 400
            for position, entry_id in enumerate(entry_ids, start=1):
                connection.execute(
                    "UPDATE entries SET sort_order = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (position, entry_id),
                )
        return jsonify({"status": "ok"})

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
