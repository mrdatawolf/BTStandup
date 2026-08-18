ALTER TABLE entries RENAME TO entries_before_history;

CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 500),
    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
    target_date TEXT NOT NULL CHECK(length(target_date) = 10),
    initials TEXT NOT NULL DEFAULT '' CHECK(length(initials) <= 5),
    notes TEXT NOT NULL DEFAULT '' CHECK(length(notes) <= 10000),
    sort_order INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO entries (
    id, name, progress, target_date, initials, notes, sort_order,
    revision, deleted_at, created_at, updated_at
)
SELECT
    id, name, progress,
    COALESCE(NULLIF(date, ''), substr(created_at, 1, 10), date('now')),
    initials, notes, sort_order, 1, NULL, created_at, updated_at
FROM entries_before_history;

DROP TABLE entries_before_history;

CREATE TABLE entry_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    changed_fields TEXT NOT NULL,
    before_values TEXT,
    after_values TEXT,
    client_id TEXT,
    occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (entry_id) REFERENCES entries(id)
);

CREATE INDEX idx_entries_target_date ON entries(target_date);
CREATE INDEX idx_entries_initials ON entries(initials);
CREATE INDEX idx_entries_deleted_at ON entries(deleted_at);
CREATE INDEX idx_entry_events_entry_id ON entry_events(entry_id, id);
