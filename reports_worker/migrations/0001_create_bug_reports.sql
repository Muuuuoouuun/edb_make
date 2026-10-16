CREATE TABLE IF NOT EXISTS bug_reports (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  app_id TEXT NOT NULL,
  app_version TEXT NOT NULL,
  platform TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'bug',
  description TEXT NOT NULL,
  context_json TEXT NOT NULL DEFAULT '{}',
  diagnostics_json TEXT,
  status TEXT NOT NULL DEFAULT 'new'
);

CREATE INDEX IF NOT EXISTS bug_reports_created_at_idx
  ON bug_reports(created_at DESC);

CREATE INDEX IF NOT EXISTS bug_reports_status_created_at_idx
  ON bug_reports(status, created_at DESC);
