from pathlib import Path
import sqlite3

import pytest

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


def test_health_and_empty_list(client):
    assert client.get("/api/health").get_json() == {"status": "ok"}
    assert client.get("/api/version").get_json() == {"version": "1.0.1"}
    assert client.get("/api/entries").get_json() == []


def test_entry_crud(client):
    created_response = client.post(
        "/api/entries",
        json={"name": "Ship MVP", "date": "2026-08-18", "initials": "PT"},
    )
    assert created_response.status_code == 201
    created = created_response.get_json()
    assert created["name"] == "Ship MVP"
    assert created["progress"] == 0
    assert created["date"] == "2026-08-18"
    assert created["initials"] == "PT"
    assert created["notes"] == ""

    updated_response = client.patch(
        f"/api/entries/{created['id']}",
        json={"name": "Ship it", "progress": 75, "notes": "Ready for review"},
    )
    assert updated_response.status_code == 200
    assert updated_response.get_json()["progress"] == 75
    assert updated_response.get_json()["name"] == "Ship it"
    assert updated_response.get_json()["notes"] == "Ready for review"
    assert client.get("/api/entries").get_json()[0]["id"] == created["id"]

    assert client.delete(f"/api/entries/{created['id']}").status_code == 204
    assert client.get("/api/entries").get_json() == []


@pytest.mark.parametrize("payload", [{}, {"name": "   "}, {"name": 42}])
def test_rejects_invalid_names(client, payload):
    assert client.post("/api/entries", json=payload).status_code == 400


@pytest.mark.parametrize("progress", [-1, 101, 1.5, True, "50"])
def test_rejects_invalid_progress(client, progress):
    created = client.post("/api/entries", json={"name": "Test"}).get_json()
    response = client.patch(
        f"/api/entries/{created['id']}", json={"progress": progress}
    )
    assert response.status_code == 400


def test_missing_entries_return_not_found(client):
    assert client.patch("/api/entries/999", json={"progress": 50}).status_code == 404
    assert client.delete("/api/entries/999").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"date": "08/18/2026"},
        {"date": "2026-02-30"},
        {"initials": "too-long"},
        {"initials": "a!"},
        {"notes": "x" * 10_001},
        {"unsupported": "value"},
    ],
)
def test_rejects_invalid_extended_fields(client, payload):
    created = client.post("/api/entries", json={"name": "Test"}).get_json()
    assert client.patch(f"/api/entries/{created['id']}", json=payload).status_code == 400


def test_reorders_entries(client):
    first = client.post("/api/entries", json={"name": "First"}).get_json()
    second = client.post("/api/entries", json={"name": "Second"}).get_json()

    response = client.put(
        "/api/entries/order", json={"entry_ids": [second["id"], first["id"]]}
    )
    assert response.status_code == 200
    assert [entry["name"] for entry in client.get("/api/entries").get_json()] == [
        "Second",
        "First",
    ]
    assert client.put(
        "/api/entries/order", json={"entry_ids": [first["id"]]}
    ).status_code == 400


def test_migrates_original_database_schema(tmp_path):
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
        connection.execute("INSERT INTO entries (name, progress) VALUES ('Existing', 25)")

    migrated_client = create_app(database_path).test_client()
    entry = migrated_client.get("/api/entries").get_json()[0]
    assert entry["name"] == "Existing"
    assert entry["date"] == ""
    assert entry["initials"] == ""
    assert entry["notes"] == ""
    assert entry["sort_order"] == entry["id"]


def test_creates_restorable_backup(client, database_path, tmp_path):
    client.post("/api/entries", json={"name": "Back me up"})
    backup_directory = tmp_path / "backups"
    backup_path = create_backup(database_path, backup_directory, 30)

    restored_client = create_app(backup_path).test_client()
    restored = restored_client.get("/api/entries").get_json()
    assert [entry["name"] for entry in restored] == ["Back me up"]

    client.post("/api/entries", json={"name": "Added later"})
    replacement_path = create_backup(database_path, backup_directory, 30)
    assert replacement_path == backup_path
    replaced_client = create_app(replacement_path).test_client()
    replaced = replaced_client.get("/api/entries").get_json()
    assert [entry["name"] for entry in replaced] == ["Back me up", "Added later"]
