PRAGMA legacy_alter_table = ON;

ALTER TABLE entries RENAME TO entries_before_optional_target_date;

CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 500),
    progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
    target_date TEXT CHECK(target_date IS NULL OR length(target_date) = 10),
    initials TEXT NOT NULL DEFAULT '' CHECK(length(initials) <= 5),
    notes TEXT NOT NULL DEFAULT '' CHECK(length(notes) <= 10000),
    sort_order INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    external_system TEXT,
    external_project_id INTEGER,
    external_project_title TEXT,
    external_project_url TEXT,
    external_progress INTEGER CHECK(external_progress BETWEEN 0 AND 100),
    external_status TEXT,
    external_synced_at TEXT,
    external_sync_error TEXT
);

INSERT INTO entries (
    id, name, progress, target_date, initials, notes, sort_order,
    revision, deleted_at, created_at, updated_at,
    external_system, external_project_id, external_project_title,
    external_project_url, external_progress, external_status,
    external_synced_at, external_sync_error
)
SELECT
    id, name, progress, target_date, initials, notes, sort_order,
    revision, deleted_at, created_at, updated_at,
    external_system, external_project_id, external_project_title,
    external_project_url, external_progress, external_status,
    external_synced_at, external_sync_error
FROM entries_before_optional_target_date;

DROP TABLE entries_before_optional_target_date;

CREATE INDEX idx_entries_target_date ON entries(target_date);
CREATE INDEX idx_entries_initials ON entries(initials);
CREATE INDEX idx_entries_deleted_at ON entries(deleted_at);
CREATE INDEX idx_entries_external_project
ON entries(external_system, external_project_id);
