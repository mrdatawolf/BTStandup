ALTER TABLE entries ADD COLUMN external_system TEXT;
ALTER TABLE entries ADD COLUMN external_project_id INTEGER;
ALTER TABLE entries ADD COLUMN external_project_title TEXT;
ALTER TABLE entries ADD COLUMN external_project_url TEXT;
ALTER TABLE entries ADD COLUMN external_progress INTEGER CHECK(external_progress BETWEEN 0 AND 100);
ALTER TABLE entries ADD COLUMN external_status TEXT;
ALTER TABLE entries ADD COLUMN external_synced_at TEXT;
ALTER TABLE entries ADD COLUMN external_sync_error TEXT;

CREATE INDEX idx_entries_external_project
ON entries(external_system, external_project_id);
