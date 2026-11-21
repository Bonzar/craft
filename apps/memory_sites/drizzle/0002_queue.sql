CREATE TABLE memory_jobs (
  id TEXT PRIMARY KEY NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('ready', 'running', 'done', 'failed')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 10),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  available_at INTEGER NOT NULL,
  lease_token TEXT UNIQUE,
  lease_expires_at INTEGER,
  checkpoint TEXT,
  result TEXT,
  failure_code TEXT,
  CHECK (
    (state = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
    OR (state <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)
  ),
  CHECK (attempts <= max_attempts)
);
--> statement-breakpoint
CREATE INDEX memory_jobs_ready ON memory_jobs(available_at, created_at, id) WHERE state = 'ready';
--> statement-breakpoint
CREATE INDEX memory_jobs_expired ON memory_jobs(lease_expires_at, id) WHERE state = 'running';
