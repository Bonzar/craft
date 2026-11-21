-- Permanent semantic storage, separate from approved snapshots and provisional
-- job publications. "payload" is the *decoded* canonical Thought object.
-- Validate canonical hashes, evidence meaning, observed heads and ownership
-- in the eventual semantic write path. SQL only enforces structural integrity.
CREATE TABLE memory_thought_versions (
  thought_id TEXT NOT NULL CHECK (length(thought_id)>0),
  version INTEGER NOT NULL CHECK (typeof(version)='integer' AND version>=1),
  payload TEXT NOT NULL CHECK (
    json_valid(payload) AND json_type(payload) IS 'object'
    AND json_type(payload,'$.id') IS 'text' AND json_extract(payload,'$.id')=thought_id
    AND json_type(payload,'$.version') IS 'integer' AND json_extract(payload,'$.version')=version
    AND json_type(payload,'$.text') IS 'text'
    AND COALESCE(json_type(payload,'$.question'),'missing') IN ('text','null')
    AND json_type(payload,'$.created_at') IS 'text'
    AND json_type(payload,'$.origin') IS 'object'
    AND json_type(payload,'$.metadata') IS 'object'
    AND json_type(payload,'$.bases') IS 'array'
  ),
  content_hash TEXT NOT NULL CHECK (length(content_hash)=64 AND content_hash NOT GLOB '*[^0-9a-f]*'),
  PRIMARY KEY (thought_id,version)
);
--> statement-breakpoint
CREATE TABLE memory_thoughts (
  id TEXT PRIMARY KEY NOT NULL,
  current_version INTEGER NOT NULL,
  FOREIGN KEY (id,current_version) REFERENCES memory_thought_versions(thought_id,version) ON DELETE RESTRICT
);
--> statement-breakpoint
CREATE TABLE memory_thought_version_bases (
  thought_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  basis_index INTEGER NOT NULL CHECK (basis_index>=0),
  basis_event_id TEXT,
  basis_thought_id TEXT,
  basis_thought_version INTEGER,
  span_start INTEGER,
  span_end INTEGER,
  content_hash TEXT NOT NULL CHECK (length(content_hash)=64 AND content_hash NOT GLOB '*[^0-9a-f]*'),
  PRIMARY KEY (thought_id,version,basis_index),
  FOREIGN KEY (thought_id,version) REFERENCES memory_thought_versions(thought_id,version) ON DELETE CASCADE,
  FOREIGN KEY (basis_event_id) REFERENCES memory_raw_captures(event_id) ON DELETE RESTRICT,
  FOREIGN KEY (basis_thought_id,basis_thought_version) REFERENCES memory_thought_versions(thought_id,version) ON DELETE RESTRICT,
  CHECK ((basis_event_id IS NOT NULL AND basis_thought_id IS NULL AND basis_thought_version IS NULL)
      OR (basis_event_id IS NULL AND basis_thought_id IS NOT NULL AND basis_thought_version IS NOT NULL)),
  CHECK ((span_start IS NULL AND span_end IS NULL)
      OR (span_start IS NOT NULL AND span_end IS NOT NULL
          AND span_start>=0 AND span_end>span_start))
);
--> statement-breakpoint
CREATE INDEX memory_thought_version_basis_by_event ON memory_thought_version_bases(basis_event_id) WHERE basis_event_id IS NOT NULL;
--> statement-breakpoint
CREATE INDEX memory_thought_version_basis_by_thought ON memory_thought_version_bases(basis_thought_id,basis_thought_version) WHERE basis_thought_id IS NOT NULL;
--> statement-breakpoint
CREATE TRIGGER memory_thought_versions_validate BEFORE INSERT ON memory_thought_versions
BEGIN
  -- Prevent a duplicate INSERT OR REPLACE from rewriting exact-version history.
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM memory_thought_versions WHERE thought_id=NEW.thought_id AND version=NEW.version
  ) THEN RAISE(ABORT,'thought version already exists') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM json_each(NEW.payload,'$.bases') AS b
    WHERE b.type<>'object'
      OR NOT (
        (json_type(b.value,'$.event_id') IS 'text'
          AND json_type(b.value,'$.thought_id') IS NULL AND json_type(b.value,'$.version') IS NULL)
        OR (json_type(b.value,'$.event_id') IS NULL
          AND json_type(b.value,'$.thought_id') IS 'text' AND json_type(b.value,'$.version') IS 'integer'
          AND json_extract(b.value,'$.version')>=1)
      )
      OR json_type(b.value,'$.content_hash') IS NOT 'text'
      OR NOT (
        json_type(b.value,'$.span') IS NULL OR json_type(b.value,'$.span') IS 'null'
        OR (json_type(b.value,'$.span') IS 'object'
          AND json_type(b.value,'$.span.start') IS 'integer' AND json_type(b.value,'$.span.end') IS 'integer'
          AND (SELECT count(*) FROM json_each(b.value,'$.span'))=2)
      )
      OR (json_type(b.value,'$.meaning') IS NOT NULL AND json_type(b.value,'$.meaning') IS NOT 'text')
      OR EXISTS (
        SELECT 1 FROM json_each(b.value) k
        WHERE k.key NOT IN ('event_id','thought_id','version','span','content_hash','meaning')
      )
  ) THEN RAISE(ABORT,'invalid pinned thought basis') END;
  -- Existence before append excludes self/forward refs independently of FK
  -- deferral; v2 citing already-present v1 is legal.
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM json_each(NEW.payload,'$.bases') AS b
    WHERE (
      json_type(b.value,'$.thought_id') IS 'text'
      AND NOT EXISTS (
        SELECT 1 FROM memory_thought_versions v
        WHERE v.thought_id=json_extract(b.value,'$.thought_id') AND v.version=json_extract(b.value,'$.version')
      )
    ) OR (
      json_type(b.value,'$.event_id') IS 'text'
      AND NOT EXISTS (
        SELECT 1 FROM memory_raw_captures c WHERE c.event_id=json_extract(b.value,'$.event_id')
      )
    )
  ) THEN RAISE(ABORT,'pinned evidence must already exist') END;
END;
--> statement-breakpoint
CREATE TRIGGER memory_thought_versions_project_bases AFTER INSERT ON memory_thought_versions
BEGIN
  INSERT INTO memory_thought_version_bases
    (thought_id,version,basis_index,basis_event_id,basis_thought_id,basis_thought_version,span_start,span_end,content_hash)
  SELECT NEW.thought_id,NEW.version,b.key,
    json_extract(b.value,'$.event_id'),json_extract(b.value,'$.thought_id'),json_extract(b.value,'$.version'),
    json_extract(b.value,'$.span.start'),json_extract(b.value,'$.span.end'),json_extract(b.value,'$.content_hash')
  FROM json_each(NEW.payload,'$.bases') b;
END;
--> statement-breakpoint
CREATE TRIGGER memory_thought_version_bases_validate BEFORE INSERT ON memory_thought_version_bases
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM memory_thought_versions v, json_each(v.payload,'$.bases') b
    WHERE v.thought_id=NEW.thought_id AND v.version=NEW.version AND b.key=NEW.basis_index
      AND json_extract(b.value,'$.event_id') IS NEW.basis_event_id
      AND json_extract(b.value,'$.thought_id') IS NEW.basis_thought_id
      AND json_extract(b.value,'$.version') IS NEW.basis_thought_version
      AND json_extract(b.value,'$.span.start') IS NEW.span_start
      AND json_extract(b.value,'$.span.end') IS NEW.span_end
      AND json_extract(b.value,'$.content_hash') IS NEW.content_hash
  ) THEN RAISE(ABORT,'basis projection must match immutable payload') END;
END;
--> statement-breakpoint
CREATE TRIGGER memory_thought_version_bases_immutable BEFORE UPDATE ON memory_thought_version_bases
BEGIN
  SELECT RAISE(ABORT,'basis projection is immutable');
END;
--> statement-breakpoint
CREATE TRIGGER memory_thought_version_bases_no_orphan_projection BEFORE DELETE ON memory_thought_version_bases
WHEN EXISTS (
  SELECT 1 FROM memory_thought_versions WHERE thought_id=OLD.thought_id AND version=OLD.version
)
BEGIN
  SELECT RAISE(ABORT,'basis projection requires owning version removal');
END;
--> statement-breakpoint
CREATE TRIGGER memory_thought_versions_immutable BEFORE UPDATE ON memory_thought_versions
BEGIN
  SELECT RAISE(ABORT,'thought version is immutable');
END;
--> statement-breakpoint
CREATE TABLE memory_thought_links (
  id TEXT PRIMARY KEY NOT NULL,
  source_id TEXT NOT NULL,
  source_version INTEGER NOT NULL,
  target_id TEXT NOT NULL,
  target_version INTEGER NOT NULL,
  payload TEXT NOT NULL CHECK (
    json_valid(payload) AND json_type(payload) IS 'object'
    AND json_type(payload,'$.id') IS 'text' AND json_extract(payload,'$.id')=id
    AND json_type(payload,'$.source_id') IS 'text' AND json_extract(payload,'$.source_id')=source_id
    AND json_type(payload,'$.target_id') IS 'text' AND json_extract(payload,'$.target_id')=target_id
    AND json_type(payload,'$.source_version') IS 'integer' AND json_extract(payload,'$.source_version')=source_version
    AND json_type(payload,'$.target_version') IS 'integer' AND json_extract(payload,'$.target_version')=target_version
    AND json_type(payload,'$.meaning') IS 'text'
  ),
  FOREIGN KEY (source_id,source_version) REFERENCES memory_thought_versions(thought_id,version) ON DELETE RESTRICT,
  FOREIGN KEY (target_id,target_version) REFERENCES memory_thought_versions(thought_id,version) ON DELETE RESTRICT
);
--> statement-breakpoint
CREATE INDEX memory_thought_link_source ON memory_thought_links(source_id);
--> statement-breakpoint
CREATE INDEX memory_thought_link_target ON memory_thought_links(target_id);
--> statement-breakpoint
CREATE TABLE memory_thought_retirements (
  id INTEGER PRIMARY KEY,
  thought_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  reason TEXT NOT NULL CHECK (length(reason)>0),
  proof TEXT NOT NULL CHECK (
    json_valid(proof) AND json_type(proof) IS 'object'
    AND json_extract(proof,'$.verification') IS 'accepted'
    AND json_type(proof,'$.expected_versions') IS 'object'
  ),
  retired_at TEXT NOT NULL,
  FOREIGN KEY (thought_id,version) REFERENCES memory_thought_versions(thought_id,version) ON DELETE RESTRICT
);
