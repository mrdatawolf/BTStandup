import sqlite3
from datetime import date
from pathlib import Path

import pytest
import app as app_module

from app import create_app
from backup import create_backup


@pytest.fixture()
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "standup.db"


@pytest.fixture()
def client(database_path: Path):
    application = create_app(database_path)
    application.config["TESTING"] = True
    return application.test_client()


def create_entry(client, name="Test", **values):
    response = client.post("/api/entries", json={"name": name, **values})
    assert response.status_code == 201
    return response.get_json()


def test_health_version_and_empty_list(client):
    assert client.get("/api/health").get_json() == {
        "status": "ok", "schema_version": 5,
    }
    assert client.get("/api/version").get_json() == {"version": "1.1.2"}
    assert client.get("/api/config").get_json() == {"issue_create_url": ""}
    assert client.get("/api/entries").get_json() == []


@pytest.mark.parametrize(("configured", "exposed"), [
    ("https://issues.example.test/new", "https://issues.example.test/new"),
    ("javascript:alert(1)", ""),
])
def test_browser_config_only_exposes_safe_issue_url(database_path, monkeypatch, configured, exposed):
    monkeypatch.setattr(app_module, "get_issue_create_url", lambda: configured)
    configured_client = create_app(database_path).test_client()
    assert configured_client.get("/api/config").get_json() == {
        "issue_create_url": exposed,
    }


def test_create_defaults_target_date_and_records_history(client):
    created = create_entry(client, "Ship MVP", initials="PT")
    assert created["target_date"] == date.today().isoformat()
    assert created["revision"] == 1
    history = client.get(f"/api/entries/{created['id']}/history").get_json()
    assert [event["event_type"] for event in history] == ["created"]
    assert history[0]["after"]["name"] == "Ship MVP"


def test_update_requires_revision_and_records_before_after(client):
    created = create_entry(client, target_date="2026-08-18")
    assert client.patch(
        f"/api/entries/{created['id']}", json={"progress": 50}
    ).status_code == 400

    response = client.patch(
        f"/api/entries/{created['id']}",
        json={"progress": 75, "notes": "Ready", "revision": created["revision"]},
        headers={"X-Client-ID": "test-browser"},
    )
    assert response.status_code == 200
    updated = response.get_json()
    assert updated["revision"] == 2
    history = client.get(f"/api/entries/{created['id']}/history").get_json()
    assert history[0]["event_type"] == "details_changed"
    assert history[0]["before"]["progress"] == 0
    assert history[0]["after"]["progress"] == 75
    assert history[0]["client_id"] == "test-browser"


def test_stale_update_returns_conflict(client):
    created = create_entry(client)
    first = client.patch(
        f"/api/entries/{created['id']}",
        json={"progress": 25, "revision": created["revision"]},
    ).get_json()
    response = client.patch(
        f"/api/entries/{created['id']}",
        json={"progress": 50, "revision": created["revision"]},
    )
    assert response.status_code == 409
    assert response.get_json()["current"]["revision"] == first["revision"]


def test_defer_week_tracks_reporting_snapshot_and_history(client):
    created = create_entry(
        client, "OPS - Replace expiring certificate",
        target_date="2026-08-21", initials="PT",
    )
    response = client.post(
        f"/api/entries/{created['id']}/defer-week",
        json={"revision": created["revision"]},
        headers={"X-Client-ID": "test-browser"},
    )
    assert response.status_code == 200
    deferred = response.get_json()
    assert deferred["target_date"] == "2026-08-28"
    assert deferred["revision"] == 2

    records = client.get("/api/schedule-deferrals").get_json()
    assert records[0] == {
        "id": 1,
        "entry_id": created["id"],
        "entry_name": "OPS - Replace expiring certificate",
        "assignee_initials": "PT",
        "title_abbreviation": "OPS",
        "previous_target_date": "2026-08-21",
        "new_target_date": "2026-08-28",
        "deferred_days": 7,
        "client_id": "test-browser",
        "occurred_at": records[0]["occurred_at"],
    }
    history = client.get(f"/api/entries/{created['id']}/history").get_json()
    assert history[0]["event_type"] == "target_deferred_one_week"
    assert history[0]["before"]["target_date"] == "2026-08-21"
    assert history[0]["after"]["target_date"] == "2026-08-28"


def test_defer_week_without_title_prefix_records_null_abbreviation(client):
    created = create_entry(client, "Plain title", target_date="2024-02-25")
    response = client.post(
        f"/api/entries/{created['id']}/defer-week",
        json={"revision": created["revision"]},
    )
    assert response.get_json()["target_date"] == "2024-03-03"
    assert client.get("/api/schedule-deferrals").get_json()[0]["title_abbreviation"] is None


def test_soft_delete_restore_and_history(client):
    created = create_entry(client)
    deleted_response = client.delete(
        f"/api/entries/{created['id']}", json={"revision": created["revision"]}
    )
    assert deleted_response.status_code == 200
    deleted = deleted_response.get_json()
    assert deleted["deleted_at"] is not None
    assert client.get("/api/entries").get_json() == []
    assert client.get("/api/entries?deleted=true").get_json()[0]["id"] == created["id"]

    restored_response = client.post(
        f"/api/entries/{created['id']}/restore",
        json={"revision": deleted["revision"]},
    )
    assert restored_response.status_code == 200
    assert restored_response.get_json()["deleted_at"] is None
    history = client.get(f"/api/entries/{created['id']}/history").get_json()
    assert [event["event_type"] for event in history[:2]] == ["restored", "deleted"]


def test_filters_and_sorts_entries(client):
    alpha = create_entry(
        client, "Alpha deployment", target_date="2026-08-20",
        initials="ZZ", notes="Database work", progress=80,
    )
    beta = create_entry(
        client, "Beta database", target_date="2026-08-10",
        initials="AA", notes="Documentation", progress=20,
    )
    assert [entry["id"] for entry in client.get(
        "/api/entries?target_date_from=2026-08-15"
    ).get_json()] == [alpha["id"]]
    assert [entry["id"] for entry in client.get("/api/entries?q=database").get_json()] == [
        alpha["id"], beta["id"],
    ]
    assert client.get("/api/entries?initials=aa").get_json()[0]["id"] == beta["id"]
    assert [entry["id"] for entry in client.get(
        "/api/entries?sort=target_date_asc"
    ).get_json()] == [beta["id"], alpha["id"]]
    assert [entry["id"] for entry in client.get(
        "/api/entries?sort=progress_desc"
    ).get_json()] == [alpha["id"], beta["id"]]


@pytest.mark.parametrize(
    "query",
    ["deleted=maybe", "target_date_from=bad", "initials=TOOLONG", "sort=bad"],
)
def test_rejects_invalid_filters(client, query):
    assert client.get(f"/api/entries?{query}").status_code == 400


def test_sse_replays_events_from_explicit_position(client):
    created = create_entry(client, "Live update")
    response = client.get("/api/events?after=0", buffered=False)
    first_message = next(response.response).decode()
    response.close()
    assert "event: entry_changed" in first_message
    assert f'"entry_id": {created["id"]}' in first_message


@pytest.mark.parametrize("payload", [{}, {"name": "   "}, {"name": 42}])
def test_rejects_invalid_names(client, payload):
    assert client.post("/api/entries", json=payload).status_code == 400


@pytest.mark.parametrize("progress", [-1, 101, 1.5, True, "50"])
def test_rejects_invalid_progress(client, progress):
    created = create_entry(client)
    response = client.patch(
        f"/api/entries/{created['id']}",
        json={"progress": progress, "revision": created["revision"]},
    )
    assert response.status_code == 400


def project_summary(project_id=5, progress=42):
    return {
        "id": project_id, "title": "Client Site", "status": "In Progress",
        "paused": False, "task_total": 24, "task_done": 10,
        "progress": progress, "updated_at": "2026-08-19T18:30:00Z",
        "web_url": f"http://projects.test/project.html?id={project_id}",
    }


def test_project_discovery_is_proxied(client, monkeypatch):
    monkeypatch.setattr(app_module, "fetch_biztech_json", lambda config, path: [project_summary()])
    response = client.get("/api/integrations/biztech-projects/projects")
    assert response.status_code == 200
    assert response.get_json()[0]["progress"] == 42


def test_project_discovery_rejects_unsafe_url(client, monkeypatch):
    unsafe = project_summary()
    unsafe["web_url"] = "javascript:alert(1)"
    monkeypatch.setattr(app_module, "fetch_biztech_json", lambda config, path: [unsafe])
    response = client.get("/api/integrations/biztech-projects/projects")
    assert response.status_code == 502
    assert "invalid project summary" in response.get_json()["error"]


def test_link_refresh_and_unlink_project(client, monkeypatch):
    current = project_summary()
    monkeypatch.setattr(app_module, "fetch_biztech_json", lambda config, path: current.copy())
    created = create_entry(client, "Management rollup", progress=15)

    linked_response = client.post(
        f"/api/entries/{created['id']}/project-link",
        json={"project_id": 5, "revision": created["revision"]},
    )
    assert linked_response.status_code == 200
    linked = linked_response.get_json()
    assert linked["external_project_id"] == 5
    assert linked["progress"] == 42
    assert linked["manual_progress"] == 15
    assert client.patch(
        f"/api/entries/{created['id']}",
        json={"progress": 75, "revision": linked["revision"]},
    ).status_code == 400

    current["progress"] = 75
    refreshed = client.post(
        f"/api/entries/{created['id']}/project-refresh",
        json={"revision": linked["revision"]},
    ).get_json()
    assert refreshed["progress"] == 75

    unlinked = client.delete(
        f"/api/entries/{created['id']}/project-link",
        json={"revision": refreshed["revision"]},
    ).get_json()
    assert unlinked["external_project_id"] is None
    assert unlinked["progress"] == 75
    assert [event["event_type"] for event in client.get(
        f"/api/entries/{created['id']}/history"
    ).get_json()[:3]] == ["project_unlinked", "project_refreshed", "project_linked"]


def test_project_link_rejects_stale_revision(client, monkeypatch):
    monkeypatch.setattr(app_module, "fetch_biztech_json", lambda config, path: project_summary())
    created = create_entry(client)
    updated = client.patch(
        f"/api/entries/{created['id']}",
        json={"notes": "changed", "revision": created["revision"]},
    ).get_json()
    response = client.post(
        f"/api/entries/{created['id']}/project-link",
        json={"project_id": 5, "revision": created["revision"]},
    )
    assert response.status_code == 409
    assert response.get_json()["current"]["revision"] == updated["revision"]


def test_reorders_entries_with_revisions(client):
    first = create_entry(client, "First")
    second = create_entry(client, "Second")
    response = client.put("/api/entries/order", json={"entries": [
        {"id": second["id"], "revision": second["revision"]},
        {"id": first["id"], "revision": first["revision"]},
    ]})
    assert response.status_code == 200
    assert [entry["name"] for entry in client.get("/api/entries").get_json()] == [
        "Second", "First",
    ]
    assert all(entry["revision"] == 2 for entry in response.get_json())


def test_migrates_original_database_and_backfills_target_date(tmp_path):
    database_path = tmp_path / "old.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "INSERT INTO entries (name, progress, created_at) VALUES (?, ?, ?)",
            ("Existing", 25, "2026-07-04 12:00:00"),
        )

    entry = create_app(database_path).test_client().get("/api/entries").get_json()[0]
    assert entry["name"] == "Existing"
    assert entry["target_date"] == "2026-07-04"
    assert entry["revision"] == 1


def test_migrates_mvp3_database(tmp_path):
    database_path = tmp_path / "mvp3.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                date TEXT NOT NULL DEFAULT '', initials TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO entries (name, date, initials, notes, sort_order)
            VALUES ('MVP3', '2026-08-18', 'PT', 'Keep this', 1);
            """
        )
    entry = create_app(database_path).test_client().get("/api/entries").get_json()[0]
    assert entry["target_date"] == "2026-08-18"
    assert entry["initials"] == "PT"
    assert entry["notes"] == "Keep this"


def test_backup_can_replace_same_day_file(client, database_path, tmp_path):
    create_entry(client, "Back me up")
    directory = tmp_path / "backups"
    backup_path = create_backup(database_path, directory, 30)
    create_entry(client, "Added later")
    assert create_backup(database_path, directory, 30) == backup_path
    restored = create_app(backup_path).test_client().get("/api/entries").get_json()
    assert [entry["name"] for entry in restored] == ["Back me up", "Added later"]
