-- Private assertion-memory capture, separate from approved graph snapshots.
CREATE TABLE memory_raw_captures (
  event_id TEXT PRIMARY KEY NOT NULL,
  session_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  position INTEGER CHECK (position >= 0),
  receipt TEXT NOT NULL,
  raw_json TEXT NOT NULL CHECK (json_valid(raw_json)),
  raw_sha256 TEXT NOT NULL CHECK (length(raw_sha256)=64),
  captured_at INTEGER NOT NULL,
  UNIQUE(session_id, message_id),
  UNIQUE(session_id, position)
);
--> statement-breakpoint
CREATE TRIGGER memory_raw_captures_enqueue AFTER INSERT ON memory_raw_captures
BEGIN
  INSERT INTO memory_jobs
    (id,kind,payload,payload_sha256,state,max_attempts,created_at,updated_at,available_at)
  VALUES
    (NEW.event_id,'source-thoughts',NEW.raw_json,NEW.raw_sha256,'ready',3,
     NEW.captured_at,NEW.captured_at,NEW.captured_at);
END;
