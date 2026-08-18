from pathlib import Path

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
    assert client.get("/api/entries").get_json() == []


def test_entry_crud(client):
    created_response = client.post("/api/entries", json={"name": "Ship MVP"})
    assert created_response.status_code == 201
    created = created_response.get_json()
    assert created["name"] == "Ship MVP"
    assert created["progress"] == 0

    updated_response = client.patch(
        f"/api/entries/{created['id']}", json={"progress": 75}
    )
    assert updated_response.status_code == 200
    assert updated_response.get_json()["progress"] == 75
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


def test_creates_restorable_backup(client, database_path, tmp_path):
    client.post("/api/entries", json={"name": "Back me up"})
    backup_path = create_backup(database_path, tmp_path / "backups", 30)

    restored_client = create_app(backup_path).test_client()
    restored = restored_client.get("/api/entries").get_json()
    assert [entry["name"] for entry in restored] == ["Back me up"]
