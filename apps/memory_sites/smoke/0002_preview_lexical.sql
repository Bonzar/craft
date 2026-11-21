-- Synthetic-preview-only corpus. Never use in the production D1 migration journal.
CREATE TABLE memory_synthetic_search_nodes (
  ordinal INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE,
  text TEXT NOT NULL,
  flags INTEGER NOT NULL
);
--> statement-breakpoint
CREATE VIRTUAL TABLE memory_synthetic_search_fts USING fts5(text);
--> statement-breakpoint
INSERT INTO memory_synthetic_search_nodes(ordinal,id,text,flags) VALUES
 (1,'fixture-east','Вымышленная команда открыла стойку на востоке.',1),
 (2,'fixture-west','Вымышленная команда перенесла стойку на запад.',0),
 (3,'fixture-lockers','Вымышленная команда предложила поставить шкафчики.',0);
--> statement-breakpoint
INSERT INTO memory_synthetic_search_fts(rowid,text)
SELECT ordinal,text FROM memory_synthetic_search_nodes;
