CREATE TABLE memory_graph_meta (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  provider_id TEXT NOT NULL,
  source_revision TEXT NOT NULL
);
--> statement-breakpoint
CREATE TABLE memory_graph_nodes (
  id TEXT PRIMARY KEY,
  payload TEXT NOT NULL
);
--> statement-breakpoint
CREATE TABLE memory_graph_edges (
  id TEXT PRIMARY KEY,
  from_id TEXT NOT NULL REFERENCES memory_graph_nodes(id),
  to_id TEXT NOT NULL REFERENCES memory_graph_nodes(id),
  payload TEXT NOT NULL
);
--> statement-breakpoint
CREATE INDEX memory_graph_edges_from ON memory_graph_edges(from_id, id);
--> statement-breakpoint
CREATE INDEX memory_graph_edges_to ON memory_graph_edges(to_id, id);
--> statement-breakpoint
CREATE TABLE memory_graph_overviews (
  id TEXT PRIMARY KEY,
  payload TEXT NOT NULL
);
--> statement-breakpoint
CREATE TABLE memory_graph_topic_links (
  id TEXT PRIMARY KEY,
  parent_id TEXT NOT NULL REFERENCES memory_graph_overviews(id),
  child_id TEXT NOT NULL REFERENCES memory_graph_overviews(id),
  payload TEXT NOT NULL
);
--> statement-breakpoint
CREATE INDEX memory_graph_topics_parent ON memory_graph_topic_links(parent_id, id);
--> statement-breakpoint
CREATE INDEX memory_graph_topics_child ON memory_graph_topic_links(child_id, id);
