import sqlite3
from contextlib import closing
from pathlib import Path

from app_config import BASE_DIR, connect_database, get_database_path


MIGRATIONS_DIR = BASE_DIR / "migrations"


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def migration_version(path: Path) -> int:
    return int(path.name.split("_", 1)[0])


def detect_existing_version(connection: sqlite3.Connection) -> int:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entries'"
    ).fetchone()
    if not table:
        return 0
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(entries)")}
    if {"target_date", "revision", "deleted_at"}.issubset(columns):
        return 3
    if {"date", "initials", "notes", "sort_order"}.issubset(columns):
        return 2
    return 1


def run_migrations(database_path: Path | None = None) -> list[int]:
    path = database_path or get_database_path()
    applied_now = []
    with closing(connect_database(path)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """
        )
        connection.commit()

        recorded = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        if not recorded:
            existing_version = detect_existing_version(connection)
            for path_item in migration_files():
                version = migration_version(path_item)
                if version <= existing_version:
                    connection.execute(
                        "INSERT INTO schema_migrations (version, filename) VALUES (?, ?)",
                        (version, path_item.name),
                    )
            connection.commit()
            recorded = set(range(1, existing_version + 1))

        available = {migration_version(path_item): path_item for path_item in migration_files()}
        unknown = recorded - available.keys()
        if unknown:
            raise RuntimeError(
                f"Database contains unknown migration versions: {sorted(unknown)}"
            )

        for version, path_item in sorted(available.items()):
            if version in recorded:
                continue
            sql = path_item.read_text(encoding="utf-8")
            escaped_name = path_item.name.replace("'", "''")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + sql
                + f"\nINSERT INTO schema_migrations (version, filename) "
                  f"VALUES ({version}, '{escaped_name}');\nCOMMIT;"
            )
            applied_now.append(version)

    return applied_now


if __name__ == "__main__":
    versions = run_migrations()
    if versions:
        print(f"Applied migrations: {', '.join(map(str, versions))}")
    else:
        print("Database schema is current.")
