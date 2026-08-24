ALTER TABLE bug_reports ADD COLUMN reporter_contact TEXT;
ALTER TABLE bug_reports ADD COLUMN consent_to_contact INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bug_reports ADD COLUMN error_code TEXT;
ALTER TABLE bug_reports ADD COLUMN failed_operation TEXT;
ALTER TABLE bug_reports ADD COLUMN resolution_note TEXT;
ALTER TABLE bug_reports ADD COLUMN resolved_at TEXT;

CREATE INDEX IF NOT EXISTS bug_reports_error_code_created_at_idx
  ON bug_reports(error_code, created_at DESC);
