import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from app import BASE_DIR, get_database_path, resolve_path


def create_backup(
    database_path: Path | None = None,
    backup_directory: Path | None = None,
    retention_days: int | None = None,
) -> Path:
    load_dotenv(BASE_DIR / ".env")
    source = database_path or get_database_path()
    destination_dir = backup_directory or resolve_path(
        os.getenv("BACKUP_DIRECTORY", "./backups")
    )
    retention = retention_days
    if retention is None:
        retention = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))

    if not source.exists():
        raise FileNotFoundError(f"Database does not exist: {source}")

    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"standup-{date.today().isoformat()}.db"
    temporary = destination.with_suffix(".db.tmp")

    try:
        with sqlite3.connect(source) as source_connection:
            with sqlite3.connect(temporary) as destination_connection:
                source_connection.backup(destination_connection)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    if retention > 0:
        cutoff = datetime.now() - timedelta(days=retention)
        for candidate in destination_dir.glob("standup-????-??-??.db"):
            if datetime.fromtimestamp(candidate.stat().st_mtime) < cutoff:
                candidate.unlink()

    return destination


if __name__ == "__main__":
    backup_path = create_backup()
    print(f"Backup created: {backup_path}")
