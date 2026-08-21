CREATE TABLE schedule_deferrals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    entry_name TEXT NOT NULL,
    assignee_initials TEXT NOT NULL,
    title_abbreviation TEXT,
    previous_target_date TEXT NOT NULL CHECK(length(previous_target_date) = 10),
    new_target_date TEXT NOT NULL CHECK(length(new_target_date) = 10),
    deferred_days INTEGER NOT NULL DEFAULT 7 CHECK(deferred_days > 0),
    client_id TEXT,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (entry_id) REFERENCES entries(id)
);

CREATE INDEX idx_schedule_deferrals_entry_id
ON schedule_deferrals(entry_id, occurred_at);

CREATE INDEX idx_schedule_deferrals_reporting
ON schedule_deferrals(occurred_at, assignee_initials, title_abbreviation);
