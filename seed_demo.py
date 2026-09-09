from datetime import datetime, timedelta, timezone
from pathlib import Path

from app_config import connect_database, get_database_path
from migrate import run_migrations


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# (name, progress, days from today the target date sits at (None = TBD),
#  initials, notes, soft-deleted)
DEMO_ENTRIES = [
    ("Redesign onboarding flow", 65, 3, "AK", "Waiting on final copy review.", False),
    ("Migrate billing to Stripe", 90, 1, "JM", "QA pass scheduled tomorrow.", False),
    ("Fix SSE reconnect bug", 100, -1, "PT", "Shipped in v1.1.4.", False),
    ("Investigate slow query on entries list", 20, 5, "AK", "", False),
    ("Draft Q4 roadmap doc", 40, None, "JM", "Blocked on leadership input.", False),
    ("Update dependency versions", 100, -3, "PT", "", False),
    ("Spike: websocket vs SSE", 10, None, "AK", "Early exploration only.", False),
    ("Write onboarding docs", 0, 10, "JM", "Not started.", False),
    ("Old spike: GraphQL gateway", 30, -10, "PT", "Abandoned in favor of REST.", True),
]


def seed(database_path: Path | None = None) -> int:
    path = database_path or get_database_path()
    run_migrations(path)
    now = utc_now()
    today = datetime.now(timezone.utc).date()

    with connect_database(path) as connection:
        connection.execute("DELETE FROM entry_events")
        connection.execute("DELETE FROM schedule_deferrals")
        connection.execute("DELETE FROM entries")
        connection.execute(
            "DELETE FROM sqlite_sequence WHERE name IN "
            "('entries', 'entry_events', 'schedule_deferrals')"
        )

        for index, (name, progress, offset, initials, notes, deleted) in enumerate(
            DEMO_ENTRIES, start=1
        ):
            target_date = (
                (today + timedelta(days=offset)).isoformat() if offset is not None else None
            )
            connection.execute(
                """
                INSERT INTO entries (
                    name, progress, target_date, initials, notes, sort_order,
                    revision, deleted_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    name, progress, target_date, initials, notes, index,
                    now if deleted else None, now, now,
                ),
            )
        connection.commit()

    return len(DEMO_ENTRIES)


if __name__ == "__main__":
    count = seed()
    print(f"Seeded {count} demo entries into {get_database_path()}")
