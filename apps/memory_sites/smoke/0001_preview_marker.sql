-- Apply only to a dedicated, disposable D1 database for the synthetic
-- processing preview. Never include in the ordinary Sites migration journal.
CREATE TABLE memory_synthetic_smoke_environment (
  singleton INTEGER PRIMARY KEY CHECK (singleton=1),
  purpose TEXT NOT NULL CHECK (purpose='private-synthetic-assertion-smoke/v1')
);
--> statement-breakpoint
INSERT INTO memory_synthetic_smoke_environment(singleton,purpose)
VALUES (1,'private-synthetic-assertion-smoke/v1');
--> statement-breakpoint
CREATE TABLE memory_synthetic_smoke_checks (
  fixture TEXT PRIMARY KEY NOT NULL CHECK (fixture IN ('accepted','rejected')),
  job_id TEXT NOT NULL UNIQUE REFERENCES memory_jobs(id) ON DELETE RESTRICT
);
