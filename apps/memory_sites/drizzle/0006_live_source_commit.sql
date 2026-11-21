-- Permanent commit receipt for the dedicated single-source runner. None of
-- these records can be produced by replaying a provisional job publication.
CREATE TABLE memory_live_source_commits (
  job_id TEXT PRIMARY KEY NOT NULL REFERENCES memory_raw_captures(event_id) ON DELETE RESTRICT,
  thought_id TEXT NOT NULL UNIQUE,
  source_sha256 TEXT NOT NULL CHECK(length(source_sha256)=64),
  lease_sha256 TEXT NOT NULL CHECK(length(lease_sha256)=64),
  thought_payload TEXT NOT NULL CHECK(json_valid(thought_payload)),
  thought_sha256 TEXT NOT NULL CHECK(length(thought_sha256)=64),
  evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64),
  committed_at INTEGER NOT NULL
);
--> statement-breakpoint
CREATE TRIGGER memory_live_source_commits_write AFTER INSERT ON memory_live_source_commits
BEGIN
  -- The server INSERT SELECT compares the actual token and exact raw bytes.
  -- Triggers keep version, head, and fenced completion all-or-nothing.
  INSERT INTO memory_thought_versions(thought_id,version,payload,content_hash)
  VALUES (NEW.thought_id,1,NEW.thought_payload,NEW.thought_sha256);
  INSERT INTO memory_thoughts(id,current_version) VALUES (NEW.thought_id,1);

  UPDATE memory_jobs SET state='done',updated_at=NEW.committed_at,result=NULL,
    lease_token=NULL,lease_expires_at=NULL,failure_code=NULL
    WHERE id=NEW.job_id AND state='running' AND kind='source-thoughts'
      AND payload_sha256=NEW.source_sha256 AND lease_expires_at>MAX(NEW.committed_at,
        CAST(strftime('%s','now') AS INTEGER)*1000+CAST(substr(strftime('%f','now'),4,3) AS INTEGER))
      AND EXISTS (
        SELECT 1 FROM memory_raw_captures c
        WHERE c.event_id=NEW.job_id AND c.raw_sha256=NEW.source_sha256 AND c.raw_json=memory_jobs.payload
          AND NOT EXISTS (SELECT 1 FROM memory_raw_captures other
            WHERE other.session_id=c.session_id AND other.event_id<>c.event_id)
      )
      AND NOT EXISTS (SELECT 1 FROM memory_job_publications p WHERE p.job_id=NEW.job_id);
  SELECT CASE WHEN changes()<>1 THEN RAISE(ABORT,'permanent source completion fence failed') END;
END;
--> statement-breakpoint
CREATE TRIGGER memory_live_source_commits_immutable BEFORE UPDATE ON memory_live_source_commits
BEGIN
  SELECT RAISE(ABORT,'source commit receipt is immutable');
END;
