ALTER TABLE bug_reports ADD COLUMN payload_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS bug_reports_payload_hash_unique_idx
  ON bug_reports(payload_hash)
  WHERE payload_hash IS NOT NULL;
