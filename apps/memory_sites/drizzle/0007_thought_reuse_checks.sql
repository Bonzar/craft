-- Exact canonical thought_questions.ensure_schema definition. The table
-- stores only trusted, separately accepted applicability claims on one
-- immutable thought version. No write route, inferred claim or fallback here.
CREATE TABLE memory_thought_reuse_checks (
  thought_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  question TEXT NOT NULL,
  frontier TEXT NOT NULL,
  proofs TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (thought_id, version, question)
);
