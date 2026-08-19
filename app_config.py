import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def get_database_path() -> Path:
    return resolve_path(os.getenv("DATABASE_PATH", "./data/standup.db"))


def get_biztech_projects_config() -> dict:
    return {
        "base_url": os.getenv("BIZTECH_PROJECTS_BASE_URL", "").strip().rstrip("/"),
        "token": os.getenv("BIZTECH_PROJECTS_TOKEN", "").strip(),
        "timeout": max(1.0, float(os.getenv("BIZTECH_PROJECTS_TIMEOUT_SECONDS", "5"))),
    }


def connect_database(database_path: Path | None = None) -> sqlite3.Connection:
    path = database_path or get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection
