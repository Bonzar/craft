CREATE TABLE memory_job_publications (
  job_id TEXT PRIMARY KEY NOT NULL REFERENCES memory_jobs(id) ON DELETE RESTRICT,
  source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
  lease_sha256 TEXT NOT NULL CHECK(length(lease_sha256)=64),
  payload TEXT NOT NULL CHECK(json_valid(payload)),
  payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
  verifier TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
  published_at INTEGER NOT NULL
);
--> statement-breakpoint
CREATE TRIGGER memory_job_publications_insert AFTER INSERT ON memory_job_publications
BEGIN
  UPDATE memory_jobs
    SET state='done', updated_at=NEW.published_at, result=NULL,
        lease_token=NULL, lease_expires_at=NULL, failure_code=NULL
    WHERE id=NEW.job_id AND state='running' AND lease_expires_at>NEW.published_at
      AND payload_sha256=NEW.source_sha256;
  SELECT CASE WHEN changes()<>1 THEN RAISE(ABORT, 'publication fence failed') END;
END;
--> statement-breakpoint
CREATE TRIGGER memory_job_publications_no_update BEFORE UPDATE ON memory_job_publications
BEGIN
  SELECT RAISE(ABORT, 'publication is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER memory_job_publications_no_delete BEFORE DELETE ON memory_job_publications
BEGIN
  SELECT RAISE(ABORT, 'publication is immutable');
END;
