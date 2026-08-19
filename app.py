import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from app_config import BASE_DIR, connect_database, get_biztech_projects_config, get_database_path
from migrate import run_migrations


ENTRY_COLUMNS = (
    "id, name, progress, target_date, initials, notes, sort_order, revision, "
    "deleted_at, created_at, updated_at, external_system, external_project_id, "
    "external_project_title, external_project_url, external_progress, external_status, "
    "external_synced_at, external_sync_error"
)
SORT_OPTIONS = {
    "manual": "sort_order ASC, id ASC",
    "target_date_asc": "target_date ASC, id ASC",
    "target_date_desc": "target_date DESC, id DESC",
    "progress_asc": "COALESCE(external_progress, progress) ASC, target_date ASC, id ASC",
    "progress_desc": "COALESCE(external_progress, progress) DESC, target_date ASC, id ASC",
    "initials_asc": "initials COLLATE NOCASE ASC, target_date ASC, id ASC",
    "recent": "updated_at DESC, id DESC",
}


@contextmanager
def open_database(database_path: Path | None = None):
    with closing(connect_database(database_path)) as connection:
        with connection:
            yield connection


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def entry_to_dict(row) -> dict:
    entry = {column: row[column] for column in (
        "id", "name", "progress", "target_date", "initials", "notes",
        "sort_order", "revision", "deleted_at", "created_at", "updated_at",
        "external_system", "external_project_id", "external_project_title",
        "external_project_url", "external_progress", "external_status",
        "external_synced_at", "external_sync_error",
    )}
    entry["manual_progress"] = entry["progress"]
    if entry["external_system"] and entry["external_progress"] is not None:
        entry["progress"] = entry["external_progress"]
    return entry


class BiztechProjectsError(Exception):
    pass


def fetch_biztech_json(config: dict, path: str):
    if not config["base_url"] or not config["token"]:
        raise BiztechProjectsError("BiztechProjects integration is not configured.")
    request_object = urllib.request.Request(
        f'{config["base_url"]}{path}',
        headers={"Authorization": f'Bearer {config["token"]}', "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request_object, timeout=config["timeout"]) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise BiztechProjectsError("BiztechProjects project was not found.") from error
        if error.code in (401, 403):
            raise BiztechProjectsError("BiztechProjects rejected the integration token.") from error
        raise BiztechProjectsError(f"BiztechProjects returned HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise BiztechProjectsError("BiztechProjects is currently unavailable.") from error


def validate_project_summary(value):
    if not isinstance(value, dict):
        raise BiztechProjectsError("BiztechProjects returned an invalid project summary.")
    project_id = value.get("id")
    progress = value.get("progress")
    web_url = value.get("web_url")
    parsed_url = urllib.parse.urlparse(web_url) if isinstance(web_url, str) else None
    if (isinstance(project_id, bool) or not isinstance(project_id, int)
            or isinstance(progress, bool) or not isinstance(progress, int)
            or not 0 <= progress <= 100
            or not isinstance(value.get("title"), str)
            or not parsed_url or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc):
        raise BiztechProjectsError("BiztechProjects returned an invalid project summary.")
    return value


def event_snapshot(entry: dict | None, fields: list[str]) -> dict | None:
    if entry is None:
        return None
    return {key: entry[key] for key in fields}


def record_event(
    connection,
    entry_id: int,
    event_type: str,
    changed_fields: list[str],
    before: dict | None,
    after: dict | None,
    client_id: str | None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO entry_events (
            entry_id, event_type, changed_fields, before_values, after_values,
            client_id, occurred_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_id,
            event_type,
            json.dumps(changed_fields),
            json.dumps(event_snapshot(before, changed_fields)) if before is not None else None,
            json.dumps(event_snapshot(after, changed_fields)) if after is not None else None,
            client_id,
            utc_now(),
        ),
    )
    return cursor.lastrowid


def create_app(database_path: Path | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["DATABASE_PATH"] = database_path or get_database_path()
    app.config["BIZTECH_PROJECTS"] = get_biztech_projects_config()
    run_migrations(app.config["DATABASE_PATH"])

    def database():
        return open_database(app.config["DATABASE_PATH"])

    def client_id() -> str | None:
        value = request.headers.get("X-Client-ID", "").strip()
        return value[:64] or None

    def fetch_entry(connection, entry_id: int):
        row = connection.execute(
            f"SELECT {ENTRY_COLUMNS} FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return entry_to_dict(row) if row else None

    def conflict_response(current: dict | None):
        return jsonify({
            "error": "This entry changed in another browser. Review and retry your change.",
            "current": current,
        }), 409

    def validate_date(value, field_name="Target date"):
        if not isinstance(value, str):
            return None, f"{field_name} must be in YYYY-MM-DD format."
        try:
            return date.fromisoformat(value).isoformat(), None
        except ValueError:
            return None, f"{field_name} must be a valid date in YYYY-MM-DD format."

    def validate_fields(payload: dict, creating: bool = False):
        allowed = {"name", "progress", "target_date", "initials", "notes"}
        if creating and "name" not in payload:
            return None, "Name is required."
        if not any(field in payload for field in allowed):
            return None, "No supported fields were provided."

        values = {}
        if "name" in payload:
            name = payload["name"]
            if not isinstance(name, str) or not name.strip():
                return None, "Name is required."
            values["name"] = name.strip()
            if len(values["name"]) > 500:
                return None, "Name must be 500 characters or fewer."

        if "progress" in payload:
            progress = payload["progress"]
            if isinstance(progress, bool) or not isinstance(progress, int):
                return None, "Progress must be a whole number."
            if not 0 <= progress <= 100:
                return None, "Progress must be between 0 and 100."
            values["progress"] = progress

        if "target_date" in payload:
            target_date, error = validate_date(payload["target_date"])
            if error:
                return None, error
            values["target_date"] = target_date

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

    def required_revision(payload):
        revision = payload.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            return None, "A valid revision is required."
        return revision, None

    def project_values(summary: dict) -> dict:
        return {
            "external_system": "biztech_projects",
            "external_project_id": summary["id"],
            "external_project_title": summary["title"][:500],
            "external_project_url": summary["web_url"][:2000],
            "external_progress": summary["progress"],
            "external_status": str(summary.get("status") or "")[:100],
            "external_synced_at": utc_now(),
            "external_sync_error": None,
        }

    @app.get("/")
    def index():
        return send_from_directory(BASE_DIR, "standup.html")

    @app.get("/app.js")
    def browser_script():
        return send_from_directory(BASE_DIR, "app.js")

    @app.get("/api/health")
    def health():
        with database() as connection:
            schema_version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
        return jsonify({"status": "ok", "schema_version": schema_version})

    @app.get("/api/version")
    def version():
        value = (BASE_DIR / "VERSION.txt").read_text(encoding="utf-8").strip()
        if value.lower().startswith("version="):
            value = value.split("=", 1)[1].strip()
        return jsonify({"version": value})

    @app.get("/api/integrations/biztech-projects/projects")
    def list_biztech_projects():
        try:
            projects = fetch_biztech_json(
                app.config["BIZTECH_PROJECTS"], "/api/integrations/projects"
            )
            if not isinstance(projects, list):
                raise BiztechProjectsError("BiztechProjects returned an invalid project list.")
            return jsonify([validate_project_summary(project) for project in projects])
        except BiztechProjectsError as error:
            return jsonify({"error": str(error)}), 502

    @app.get("/api/entries")
    def list_entries():
        clauses = []
        parameters = []

        deleted = request.args.get("deleted", "false").lower()
        if deleted == "false":
            clauses.append("deleted_at IS NULL")
        elif deleted == "true":
            clauses.append("deleted_at IS NOT NULL")
        elif deleted != "all":
            return jsonify({"error": "deleted must be false, true, or all."}), 400

        for query_name, operator in (("target_date_from", ">="), ("target_date_to", "<=")):
            value = request.args.get(query_name)
            if value:
                validated, error = validate_date(value, query_name)
                if error:
                    return jsonify({"error": error}), 400
                clauses.append(f"target_date {operator} ?")
                parameters.append(validated)

        query = request.args.get("q", "").strip()
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(name LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\')")
            parameters.extend((f"%{escaped}%", f"%{escaped}%"))

        initials = request.args.get("initials", "").strip().upper()
        if initials:
            if not re.fullmatch(r"[A-Z0-9]{1,5}", initials):
                return jsonify({"error": "Invalid initials filter."}), 400
            clauses.append("initials = ?")
            parameters.append(initials)

        sort = request.args.get("sort", "manual")
        if sort not in SORT_OPTIONS:
            return jsonify({"error": "Invalid sort option."}), 400

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with database() as connection:
            rows = connection.execute(
                f"SELECT {ENTRY_COLUMNS} FROM entries{where} ORDER BY {SORT_OPTIONS[sort]}",
                parameters,
            ).fetchall()
        return jsonify([entry_to_dict(row) for row in rows])

    @app.post("/api/entries")
    def add_entry():
        payload = request.get_json(silent=True) or {}
        if not payload.get("target_date"):
            payload["target_date"] = date.today().isoformat()
        values, error = validate_fields(payload, creating=True)
        if error:
            return jsonify({"error": error}), 400

        now = utc_now()
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            next_order = connection.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM entries"
            ).fetchone()[0]
            cursor = connection.execute(
                """
                INSERT INTO entries (
                    name, progress, target_date, initials, notes, sort_order,
                    revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    values["name"], values.get("progress", 0), values["target_date"],
                    values.get("initials", ""), values.get("notes", ""),
                    next_order, now, now,
                ),
            )
            entry = fetch_entry(connection, cursor.lastrowid)
            record_event(
                connection,
                entry["id"],
                "created",
                [
                    "name", "progress", "target_date", "initials", "notes",
                    "sort_order", "revision", "deleted_at",
                ],
                None,
                entry,
                client_id(),
            )
        return jsonify(entry), 201

    @app.patch("/api/entries/<int:entry_id>")
    def update_entry(entry_id: int):
        payload = request.get_json(silent=True) or {}
        revision, error = required_revision(payload)
        if error:
            return jsonify({"error": error}), 400
        values, error = validate_fields(payload)
        if error:
            return jsonify({"error": error}), 400

        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = fetch_entry(connection, entry_id)
            if before is None or before["deleted_at"] is not None:
                return jsonify({"error": "Entry not found."}), 404
            if before["revision"] != revision:
                return conflict_response(before)
            if "progress" in values and before["external_system"]:
                return jsonify({"error": "Progress is managed by the linked project."}), 400
            changed = [field for field, value in values.items() if before[field] != value]
            if not changed:
                return jsonify(before)
            assignments = ", ".join(f"{field} = ?" for field in changed)
            now = utc_now()
            connection.execute(
                f"UPDATE entries SET {assignments}, revision = revision + 1, "
                "updated_at = ? WHERE id = ? AND revision = ?",
                (*(values[field] for field in changed), now, entry_id, revision),
            )
            after = fetch_entry(connection, entry_id)
            event_type = "progress_changed" if changed == ["progress"] else "details_changed"
            record_event(connection, entry_id, event_type, changed, before, after, client_id())
        return jsonify(after)

    @app.post("/api/entries/<int:entry_id>/project-link")
    def link_project(entry_id: int):
        payload = request.get_json(silent=True) or {}
        revision, error = required_revision(payload)
        if error:
            return jsonify({"error": error}), 400
        project_id = payload.get("project_id")
        if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id < 1:
            return jsonify({"error": "A valid project_id is required."}), 400
        try:
            summary = validate_project_summary(fetch_biztech_json(
                app.config["BIZTECH_PROJECTS"],
                f"/api/integrations/projects/{project_id}/summary",
            ))
        except BiztechProjectsError as integration_error:
            return jsonify({"error": str(integration_error)}), 502
        if summary["id"] != project_id:
            return jsonify({"error": "BiztechProjects returned the wrong project."}), 502

        values = project_values(summary)
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = fetch_entry(connection, entry_id)
            if before is None or before["deleted_at"] is not None:
                return jsonify({"error": "Entry not found."}), 404
            if before["revision"] != revision:
                return conflict_response(before)
            fields = list(values)
            assignments = ", ".join(f"{field} = ?" for field in fields)
            connection.execute(
                f"UPDATE entries SET {assignments}, revision = revision + 1, updated_at = ? "
                "WHERE id = ? AND revision = ?",
                (*(values[field] for field in fields), utc_now(), entry_id, revision),
            )
            after = fetch_entry(connection, entry_id)
            record_event(connection, entry_id, "project_linked", fields, before, after, client_id())
        return jsonify(after)

    @app.post("/api/entries/<int:entry_id>/project-refresh")
    def refresh_project(entry_id: int):
        payload = request.get_json(silent=True) or {}
        revision, error = required_revision(payload)
        if error:
            return jsonify({"error": error}), 400
        with database() as connection:
            before_fetch = fetch_entry(connection, entry_id)
        if before_fetch is None or before_fetch["deleted_at"] is not None:
            return jsonify({"error": "Entry not found."}), 404
        if before_fetch["revision"] != revision:
            return conflict_response(before_fetch)
        if before_fetch["external_system"] != "biztech_projects":
            return jsonify({"error": "Entry is not linked to a BiztechProjects project."}), 400
        try:
            summary = validate_project_summary(fetch_biztech_json(
                app.config["BIZTECH_PROJECTS"],
                f'/api/integrations/projects/{before_fetch["external_project_id"]}/summary',
            ))
        except BiztechProjectsError as integration_error:
            return jsonify({"error": str(integration_error), "current": before_fetch}), 502

        values = project_values(summary)
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = fetch_entry(connection, entry_id)
            if before is None or before["deleted_at"] is not None:
                return jsonify({"error": "Entry not found."}), 404
            if before["revision"] != revision:
                return conflict_response(before)
            fields = list(values)
            assignments = ", ".join(f"{field} = ?" for field in fields)
            connection.execute(
                f"UPDATE entries SET {assignments}, revision = revision + 1, updated_at = ? "
                "WHERE id = ? AND revision = ?",
                (*(values[field] for field in fields), utc_now(), entry_id, revision),
            )
            after = fetch_entry(connection, entry_id)
            record_event(connection, entry_id, "project_refreshed", fields, before, after, client_id())
        return jsonify(after)

    @app.delete("/api/entries/<int:entry_id>/project-link")
    def unlink_project(entry_id: int):
        payload = request.get_json(silent=True) or {}
        revision, error = required_revision(payload)
        if error:
            return jsonify({"error": error}), 400
        fields = [
            "external_system", "external_project_id", "external_project_title",
            "external_project_url", "external_progress", "external_status",
            "external_synced_at", "external_sync_error",
        ]
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = fetch_entry(connection, entry_id)
            if before is None or before["deleted_at"] is not None:
                return jsonify({"error": "Entry not found."}), 404
            if before["revision"] != revision:
                return conflict_response(before)
            if not before["external_system"]:
                return jsonify({"error": "Entry is not linked to a project."}), 400
            connection.execute(
                "UPDATE entries SET progress = ?, external_system = NULL, "
                "external_project_id = NULL, external_project_title = NULL, "
                "external_project_url = NULL, external_progress = NULL, external_status = NULL, "
                "external_synced_at = NULL, external_sync_error = NULL, "
                "revision = revision + 1, updated_at = ? WHERE id = ? AND revision = ?",
                (before["progress"], utc_now(), entry_id, revision),
            )
            after = fetch_entry(connection, entry_id)
            record_event(
                connection, entry_id, "project_unlinked", ["progress", *fields],
                before, after, client_id(),
            )
        return jsonify(after)

    @app.delete("/api/entries/<int:entry_id>")
    def delete_entry(entry_id: int):
        payload = request.get_json(silent=True) or {}
        revision, error = required_revision(payload)
        if error:
            return jsonify({"error": error}), 400
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = fetch_entry(connection, entry_id)
            if before is None or before["deleted_at"] is not None:
                return jsonify({"error": "Entry not found."}), 404
            if before["revision"] != revision:
                return conflict_response(before)
            now = utc_now()
            connection.execute(
                "UPDATE entries SET deleted_at = ?, updated_at = ?, revision = revision + 1 "
                "WHERE id = ? AND revision = ?",
                (now, now, entry_id, revision),
            )
            after = fetch_entry(connection, entry_id)
            record_event(connection, entry_id, "deleted", ["deleted_at"], before, after, client_id())
        return jsonify(after)

    @app.post("/api/entries/<int:entry_id>/restore")
    def restore_entry(entry_id: int):
        payload = request.get_json(silent=True) or {}
        revision, error = required_revision(payload)
        if error:
            return jsonify({"error": error}), 400
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            before = fetch_entry(connection, entry_id)
            if before is None or before["deleted_at"] is None:
                return jsonify({"error": "Deleted entry not found."}), 404
            if before["revision"] != revision:
                return conflict_response(before)
            now = utc_now()
            connection.execute(
                "UPDATE entries SET deleted_at = NULL, updated_at = ?, revision = revision + 1 "
                "WHERE id = ? AND revision = ?",
                (now, entry_id, revision),
            )
            after = fetch_entry(connection, entry_id)
            record_event(connection, entry_id, "restored", ["deleted_at"], before, after, client_id())
        return jsonify(after)

    @app.put("/api/entries/order")
    def reorder_entries():
        payload = request.get_json(silent=True) or {}
        items = payload.get("entries")
        if not isinstance(items, list) or any(
            not isinstance(item, dict)
            or isinstance(item.get("id"), bool) or not isinstance(item.get("id"), int)
            or isinstance(item.get("revision"), bool) or not isinstance(item.get("revision"), int)
            for item in items
        ):
            return jsonify({"error": "entries must contain IDs and revisions."}), 400
        ids = [item["id"] for item in items]
        if len(ids) != len(set(ids)):
            return jsonify({"error": "Entry IDs must be unique."}), 400

        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT {ENTRY_COLUMNS} FROM entries WHERE deleted_at IS NULL ORDER BY sort_order, id"
            ).fetchall()
            current = {row["id"]: entry_to_dict(row) for row in rows}
            if set(ids) != set(current):
                return jsonify({"error": "Manual order must include every active entry."}), 400
            for item in items:
                if current[item["id"]]["revision"] != item["revision"]:
                    return conflict_response(current[item["id"]])

            now = utc_now()
            updated = []
            for position, item in enumerate(items, start=1):
                before = current[item["id"]]
                if before["sort_order"] != position:
                    connection.execute(
                        "UPDATE entries SET sort_order = ?, revision = revision + 1, "
                        "updated_at = ? WHERE id = ? AND revision = ?",
                        (position, now, item["id"], item["revision"]),
                    )
                    after = fetch_entry(connection, item["id"])
                    record_event(
                        connection, item["id"], "reordered", ["sort_order"],
                        before, after, client_id(),
                    )
                    updated.append(after)
                else:
                    updated.append(before)
        return jsonify(updated)

    @app.get("/api/entries/<int:entry_id>/history")
    def entry_history(entry_id: int):
        with database() as connection:
            if fetch_entry(connection, entry_id) is None:
                return jsonify({"error": "Entry not found."}), 404
            rows = connection.execute(
                """
                SELECT id, entry_id, event_type, changed_fields, before_values,
                       after_values, client_id, occurred_at
                FROM entry_events WHERE entry_id = ? ORDER BY id DESC
                """,
                (entry_id,),
            ).fetchall()
        return jsonify([{
            "id": row["id"],
            "entry_id": row["entry_id"],
            "event_type": row["event_type"],
            "changed_fields": json.loads(row["changed_fields"]),
            "before": json.loads(row["before_values"]) if row["before_values"] else None,
            "after": json.loads(row["after_values"]) if row["after_values"] else None,
            "client_id": row["client_id"],
            "occurred_at": row["occurred_at"],
        } for row in rows])

    @app.get("/api/events")
    def event_stream():
        supplied_last_id = request.headers.get("Last-Event-ID") or request.args.get("after")
        try:
            last_id = int(supplied_last_id) if supplied_last_id is not None else None
        except ValueError:
            return jsonify({"error": "Last-Event-ID must be an integer."}), 400
        if last_id is None:
            with database() as connection:
                last_id = connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM entry_events"
                ).fetchone()[0]
        heartbeat = max(5, int(os.getenv("SSE_HEARTBEAT_SECONDS", "20")))

        @stream_with_context
        def generate():
            current_id = last_id
            last_heartbeat = time.monotonic()
            while True:
                with database() as connection:
                    events = connection.execute(
                        "SELECT id, entry_id, event_type, occurred_at FROM entry_events "
                        "WHERE id > ? ORDER BY id LIMIT 100",
                        (current_id,),
                    ).fetchall()
                if events:
                    for event in events:
                        current_id = event["id"]
                        data = json.dumps({
                            "entry_id": event["entry_id"],
                            "event_type": event["event_type"],
                            "occurred_at": event["occurred_at"],
                        })
                        yield f"id: {current_id}\nevent: entry_changed\ndata: {data}\n\n"
                    last_heartbeat = time.monotonic()
                elif time.monotonic() - last_heartbeat >= heartbeat:
                    yield ": heartbeat\n\n"
                    last_heartbeat = time.monotonic()
                time.sleep(1)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


if __name__ == "__main__":
    from waitress import serve

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    threads = int(os.getenv("SERVER_THREADS", "32"))
    print(f"Starting BT Standup at http://{host}:{port}")
    print(f"Database: {get_database_path()}")
    serve(create_app(), host=host, port=port, threads=threads)
