# Local graph service

Requires Python 3.12+ and `apps/graph_viewer/requirements.txt`. Run from the repository root:

```sh
python -m apps.graph_viewer.server \
  --source /path/to/source.sqlite3 \
  --state-dir /path/to/private/viewer-state \
  --password-file /path/to/private/owner-password \
  --static-dir apps/graph_viewer/web/dist \
  --port 8791
```

The password file must have mode 600 and contain an owner password of at least twelve characters. Alternatively, provide `GRAPH_VIEWER_PASSWORD` for initial setup. There is no default credential. Existing owner credentials stay in the viewer state; passing a different bootstrap password does not rotate them. Sessions and bookmarks persist in that state. Use a dedicated state directory: its directory permissions become 700 and private files become 600.

The service binds only loopback. For an explicitly configured HTTPS tunnel, add `--public-origin https://your-host.example`. This enables the exact browser origin, HTTPS-only cookies and HSTS without trusting arbitrary forwarded headers. Keep the proxy/tunnel on the same machine. No private graph data is uploaded by the service.

Add `--synthetic-fixture` for synthetic QA data so capabilities/UI can label it explicitly. This flag does not infer or alter source semantics.

## API and preparation

The frozen wire contract is `../docs/contract.json`, version 1.0.1. All private `/api/v1` routes require the HttpOnly owner session. `POST /session` accepts a password in the JSON body. Bookmark writes and logout additionally require the exact Origin and `X-CSRF-Token` returned by login/`GET /session`. Health and session authentication status are the only public reads.

`GET /graph` initially returns 202 with visible preparation status. Once ready, binary node/edge buffers have exact slot counts, finite positions, revision-scoped ordinals, ETags and private caching. Prepared gzip files retain accurate compressed Content-Length. Node status filtering does not renumber slots. Source membership/DAG data drives deterministic geometric packing; geometry carries no semantic authority.

Text, edge quotes and full saved sources are pinned to immutable local snapshot revisions. Text offsets are Unicode codepoints. Focused source reads accept paired `focus_start`/`focus_end`; all pages expose continuation. Incident edge cursors bind the exact revision, node and visibility mode.

## Semantic search

Configure the same model as the source when available:

```sh
# Append to the service command:
--embedding-model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
--embedding-cache /path/to/already-downloaded/public-model-files
```

FastEmbed uses local files only, two inference threads, and a query warmup. The service reads matching source `memory_embedding_cache` vectors without modifying that cache, then maintains its own digest/model-specific vector cache and memory-mapped matrix. Missing vectors are computed locally. Semantic indexing runs separately from graph/card/keyword readiness; preparation metrics and progress are exposed explicitly. An unavailable model/index yields semantic 503, never lexical results labeled semantic. Initial model downloads must be done separately into viewer-owned model storage.

## Read-only boundary and live updates

The assertion-memory adapter opens URI `mode=ro`, enables `query_only`, and reads each source snapshot in one transaction. It never invokes source store/engine/queue constructors or generation. Source raw data and projection caches are local sidecar files. The declaration helper mirrors the source pure helper without importing mutating constructors.

A persistent read-only watcher polls every 350 ms and observes SQLite `data_version`, source state, source disappearance and atomic file replacement. Append-compatible publications clone prepared cache pages on APFS/Linux copy-on-write filesystems, read only new source rows, patch stable binary slots, and recompute affected semantic components. Other material changes reset to a new immutable snapshot. Source view publication changes invalidate even without a source-version bump. Known-then streams remain pinned. Sessions are rechecked during streams, and logout terminates them.

Cancellation is supported only through an explicit provider extension: assertion metadata `viewer_status: {disposition: 'cancelled', published: true}`. The actual assertion-memory core does not publish global cancellation semantics. Capabilities state this limitation; no keyword-based cancellation is inferred.

## Verification

```sh
python -m unittest discover -s apps/graph_viewer/tests/backend -v
```

The backend suite uses synthetic fixtures. A configured FastEmbed model, physical devices, and network performance require separate verification.

## Private Sites reverse proxy (contract addendum 1.1)

Keep the source service on loopback. Configure the browser origin independently from the incoming HTTPS bridge hostname:

```sh
# Append to the service command:
--public-origin https://your-private-site.example \
--allowed-proxy-host your-laptop-bridge.example \
--bridge-read-token-file /path/to/private/bridge-read-token
```

`--allowed-proxy-host` is repeatable and accepts only exact DNS hostnames, without schemes, paths, ports or wildcards. The explicit browser origin still controls Origin/CSRF validation; arbitrary forwarded host/proto headers remain untrusted. With no bridge options, local-only defaults remain unchanged.

The optional read token must be a randomly generated secret of 32–512 printable ASCII bytes in an owner-only regular file (mode 600). It is loaded at startup and retained only as a SHA-256 digest for constant-time comparison. Restart the service when rotating it. Never put its value in CLI arguments, URLs, browser code, archives or logs. The native Sites secret binding may hold the matching value for server-to-server requests; the Worker must discard browser Authorization and inject this credential only on allowed reads.

A valid bearer authorizes only GET/HEAD on supported capabilities, graph, node/card/adjacency, edge/card, source, search, events and bookmark-list routes. Unknown management routes and all mutations still require normal owner authority. A bearer does not authenticate `GET /session`, return a CSRF token, mint/revoke owner sessions, or permit bookmark writes. `GET /session` continues to report the real owner cookie state. Existing owner-cookie writes require the real CSRF token and exact Sites Origin even when a valid read bearer is present. Cookie-free bearer SSE is revalidated during the stream; it remains separate from revocable owner sessions.

Only the UI and reverse-proxy Worker are hosted on the private Site. The Worker must stream graph responses, forward only `gv_session` cookies, preserve host-only Secure HttpOnly SameSite=Strict Set-Cookie, and strip platform identity/bypass/forwarding headers before upstream requests. Audience/identity checks, native secret setup, fixed-target proxy validation and publication belong to the Sites owner integration. Source SQLite files, prepared graph data and private text must never enter the uploaded artifact.

## Restart recovery

A persisted owner cookie remains valid across service lifespans. Unknown in-memory snapshot revisions are lazily restored only from matching, owner-private cache directories with canonical UUID identifiers. Restoration validates the provider/schema, selector, binary lengths and ordinal table counts. Cards keep their exact saved text, status and ordinal mapping, including pinned known-then perspectives.

A valid old SSE cursor beyond the restarted replay journal receives a finite `200 text/event-stream` reset with `reason=cursor_expired`, the new cursor and the original selector's manifest URL. This allows native EventSource to recover through the existing reset flow. Pinned known-then clients can retain their restored scene/card and reconnect for availability without mixing later knowledge. Negative, nondecimal and oversized cursors remain errors.

Completed vector matrices now have a private, atomically published `semantic.json` descriptor. Restoration requires the configured model identity, exact node/dimension/byte shape, a matching finite query warmup and finite vector data. Invalid or legacy vector descriptors fall back to normal model-specific digest-cache preparation; an unavailable or incompatible model remains explicit rather than relabeling old vectors.

Deleted, corrupt, insecure or wrong-provider snapshot caches still return `409 revision_expired`. The backend does not invent missing pinned text or reinterpret old ordinals. Complete cache loss still requires the client to re-bootstrap its graph and resolve stable IDs under its retained selector; this recovery path does not claim to reconstruct unavailable historical artifacts.

## Controlled live phase profiling

Authenticated `GET /capabilities` includes a bounded `live_profile` ring (16 refreshes). Records contain only numeric viewer cursors, timestamps and aggregate durations: source-change detection, incremental worker, snapshot setup, COW clone, new rows, affected status, binary patch, gzip, counts/bounds, metadata commit, event emission, first authorized SSE yield, and asynchronous semantic preparation. Semantic timings include waiting for the model gate and are separate from event publication.

`first_sse_yield_at_ms` marks the first matching authorized stream yield, not browser receipt or paint. A controlled client must separately record source commit, received SSE, worker/apply completion and two animation frames before attributing latency. No source IDs, text, paths or credentials enter these records. Source changes requiring a full reset have aggregate worker/refresh timings rather than an incremental phase breakdown.

## Append-only projection extrema

Prepared projections retain exact unpadded extrema in `layout.raw_bounds`. Compatible appends union those extrema with only the inserted node positions, then apply the existing padding once. Padding never compounds across revisions. Status-hidden physical slots still participate in transport bounds; history/status changes without new positions retain the previous bounds exactly. Removals and incompatible source changes rebuild the projection.

The unchanged current-date predicate `julianday(valid_at)>julianday(?)` uses a viewer-only expression index created during projection preparation, preserving NULL and clock-only semantics without scanning fat cached node rows. Legacy derived snapshots initialize that index and, on their first positional append, reconstruct exact extrema with one full cached SQL scan. No index or schema change is made in the source database. Source MAX/COUNT consistency checks remain exact inside the pinned source read transaction; their cold I/O cost is not claimed constant-time.

## Independent JSON snapshot source

`JsonSnapshotSource(input_path, state_dir)` is an optional second provider accepted
by `create_app(None, state_dir, source=source, ...)`. The normal CLI/default source
remains assertion-memory SQLite. The JSON provider captures a **frozen** input at
construction, advertises `live=false`, and requires a new adapter instance to
accept replacement input. It does not watch or rewrite that input.

The input is one UTF-8 JSON document (maximum 64 MiB) with
`schema="graph-viewer-source/v1"`, a stable `provider_id`, supplied
`source_revision`, and `nodes`, `edges`, `episodes`, `overviews` arrays; optional
`topic_links` defaults to empty. See `../tests/fixtures/independent-source.json`
for a directly authored synthetic example. IDs are exact source strings, scoped
by provider identity; moving the input file does not change that namespace.

- A node has `id`, exact nonempty `text`, `recorded_at`; optional `kind` is a
  recognized source kind or null (unknown). Supported kinds are `assertion`,
  `decision`, `observation`, `implementation`, `inference`, `proposal`,
  `requirement`, `plan`, `question`, `fact`. Missing/null kinds remain unknown;
  wording never promotes them to decisions. Optional `actor`, `source`,
  `episode_id`, `scope`, `valid_at`, `source_recorded_at`, `source_available_at`,
  `interpreted_at` retain source values. `valid_precision` is
  `unknown|instant|day|month|year`; null validity stays unknown. Exact
  `evidence_refs` are `{episode_id,start,end}` in Unicode codepoints, checked
  against supplied saved text. `context_episode_ids` contains explicit saved
  references only; `context_truncated` may be true, false or null. The optional
  `published_status` supports only `{published:true,disposition:"cancelled"}`.
- An edge has `id`, `from_id`, `to_id`, literal `kind`, `recorded_at`, optional
  `source_quote`, `target_quote`, `condition`, `basis`, `effective_at`.
  Parallel edges and direction are preserved. Unknown relation kinds have no
  invented modifier effect; `supersedes` requires explicit endpoint scopes.
- A saved episode has `id`, exact `text`, `recorded_at`, optional `actor`,
  `source`, `session_id`, `position`, `source_available_at`,
  `context_truncated`, `context_episode_ids`. Source context comes only from the
  explicit context IDs, never inferred adjacent positions. A node's unavailable
  `episode_id` remains an unavailable source; evidence offsets require an actual
  saved episode and cannot point at a placeholder.
- An overview has `id`, `title`, optional `question`, `scopes`, `versions`,
  `current_version_id`. Each version has `version_id`, `published_at`,
  `member_ids`, optional exact saved `summary` text (or null/absent),
  `valid_cutoff` and `recorded_cutoff` for a supplied exact
  historical materialization. Other historical memberships report partialness.
  Numeric public version keys are technical IR keys; exact source version IDs
  are retained separately in `source_ref.source_version_id`.
- A topic link has `id`, `parent_id`, `child_id`,
  `relation=subquestion|prerequisite|related`, `recorded_at`, nonempty object
  `provenance`. Only explicitly supplied `subquestion` links create hierarchy;
  cycles are rejected. No memory planner routes or retrieval questions are made.

Knowledge/publication timestamps require ISO-8601 instants with timezones;
`valid_at` and edge `effective_at` additionally accept ISO dates. Raw spellings
and source precision are retained. Required knowledge timestamps are never
replaced with import time. Duplicate IDs/keys, missing endpoints/members,
invalid spans, unsupported kind/status, and nonfinite/out-of-range numbers fail
before a generation is published, with bounded schema-location errors.

The adapter validates one open-file capture and binds its exact byte SHA-256 to
`manifest.source_revision` inside the same pinned read transaction as the graph.
It atomically publishes a private normalized SQLite compatibility IR under
`state_dir/source-ir/<provider-hash>/<input-sha256>/source.sqlite3` (directories
700, file 600). `connect()` uses URI `mode=ro&immutable=1` and `query_only`.
Legacy `memory_*` IR table names satisfy the current SQL projection interface;
this proves source independence while retaining SQLite as an internal viewer
dependency. No memory-engine constructors, source database, embeddings, planner
history or facts are generated. Semantic search uses the existing separately
configured viewer model; without one it returns the normal unavailable response.

`test_json_source.py` covers the public API, source independence, and source immutability.


## Saved source overviews

`GET`/`HEAD /api/v1/overviews/{URL-encoded stable topic ID}?revision=...`
reads only the selected source version's saved `snapshot.summary`, when that
value is a string. It requires an owner session or the configured read bearer.
The source text is copied verbatim into the viewer-owned projection cache;
it is never generated from a title, query, membership or another version.
Global `topics` JSON contains no summary text. Append-compatible cache clones
carry the overview table unchanged; freshness comes from the requested pinned
graph's topic metadata. Historical selection and restored snapshots retain their
selected source reference and text.

The response is `{topic_id, revision, provider_id, source_ref, available_at,
freshness, text, offset, text_total, next_offset, text_revision,
offset_unit:"unicode_codepoints"}`. `source_ref` is the authoritative selected
Topic reference, including numeric `version_id`, nullable `route_revision` and
optional opaque `source_version_id`. `text_revision` is SHA-256 of the complete
saved UTF-8 text and remains constant across pages. `offset` defaults to zero;
`limit` defaults to 12000 and accepts 1–64000 Unicode codepoints. Every page keeps
the same topic, graph revision, provider and source-version binding. A supplied
empty string is an available empty overview.

An existing topic with no saved string body, or a legacy projection cache with
no overview table, returns `404 overview_unavailable`. An unknown topic returns
`404 not_found`; absent/expired graph revisions retain the existing API errors.
No overview is invented to fill these cases. The optional JSON source accepts
`summary` on each supplied overview version; absent/null stays unavailable.

## Source component geometry (layout v3)

A fresh snapshot packs **weak connected components of the published assertion
arcs** into separate physical placement bins, with a 60-unit gap and 30-unit
visual margin. Direction is ignored only for this initial connectivity
calculation: every original directed arc, parallel arc, self-loop, node and
source membership remains unchanged. Each initial component is translated as
a whole, preserving its internal topic/jitter geometry. A singleton remains
one ordinary star. Bins are derived geometry; they supply no semantic group,
label, weight or claim about current connectivity.

Variable-size shelves pack the reserved boxes without pairwise collision
searches. Initial connectivity streams all arcs with O(nodes) Python state;
ordering bins adds O(bins log bins) work. Source topic centers and spherical
radii are recomputed from their exact members, with every member contained.
Topics can span multiple bins and overlap each other. Their root proxies remain
source-topic aggregates; separated stars are visible in the existing
“Все звёзды” mode, without a promise of disjoint topic envelopes or proxies.

Live source bridges appear as their exact new arcs between the existing stars;
old positions and placement bins stay fixed. A current weak component can
therefore occupy several old bins connected by real source arcs. Connected
fragments of newly added stars choose a deterministic bin actually reached by
their new-to-old source arcs, or a fresh bin when they have no old neighbor.
Placement reads new arcs and cached endpoint bins, without traversing old arcs
or rebuilding the graph for a bridge. It never infers additional source edges.

The manifest version is `source-component-bins-v3`. Current projections with
another layout version refresh even when the source is unchanged. Explicitly
requested old revision caches remain readable with their original geometry;
a normal restart builds a fresh current projection. Missing or corrupt derived
placement metadata uses the existing full snapshot reset path. Layout changes
do not alter the existing model/text digest semantic cache.

The component-scale backend tests verify generated 100,000-node layouts and streaming connectivity across one million directed edges.

In the recorded 100k/1M probe, three ordinary append publishers took 120–184 ms
and three bridge publishers took 130–165 ms. All three bridges retained the
old node buffer byte for byte and matched the source's exact new arc and total
edge count. These are backend timings; they do not establish browser paint
latency or the full two-second live acceptance result.


## Source interpretation membership

The assertion-memory provider selects interpretation membership using the core's
read-only `source_revision.selection` helper, imported only inside that provider.
Selection runs in the same pinned read transaction as source rows, counters,
watermarks and saved topics. The pinned maximum extraction-run ID establishes all
known ownership; the cutoff is `captured_at` for current/known-now and `at` for
known-then. This keeps future-valid active knowledge while preventing backdated
interpretations from appearing before their publication. A missing ledger keeps
legacy membership; a malformed present ledger fails preparation closed.

Withdrawn interpretations are excluded before counters, node insertion, search,
edges and memberships, including with `history=1`. History still exposes eligible
domain superseded/cancelled assertions. Interpretation withdrawal creates no
world `supersedes` edge or cancellation and changes no source metadata. The
current card returns 404; immutable source identity/bookmarks can still resolve
as `hidden_in_slice`. Known-then before withdrawal and exact prepared old
revisions retain their original cards, text, sources, binary slots and topics.

Saved topic prose and citations stay verbatim, bound to their original source
version. If source membership removes a saved member, the topic becomes stale
and partial with `source_topic_membership_filtered_by_revision`; newly introduced
assertions do not automatically become members. This applies even when a saved
historical materialization's checked version matches state. A removed citation
can be unavailable in the current perspective; use an exact old revision or
known-then. This adds no audit-history browser or source-topic regeneration.

The existing `manifest.preparation` object records
`source_membership_policy="source-selection-v1"`; no frontend schema or visual
feature changes are needed. The private `watermarks.json` token fences append
reuse on both selection fingerprint and maximum run ID. Ledger-only changes
(including writes without a state bump), or ledger plus otherwise compatible
row appends, publish a full reset. Missing policy/token prevents append reuse;
explicit legacy revisions remain exact and carry
`legacy_source_membership_unchecked` in readiness. Startup prepares fresh current
knowledge. The independent JSON provider supplies unrestricted membership bound
to its captured bytes and imports no source engine.

Receipt replay adds work proportional to ledger/proof volume during publication;
no scale or latency claim is established here. Existing live clocks refresh
world effective-time boundaries, not arbitrary future-dated publication release.
No wall-clock activation claim for future `completed_at` is made. Regression
coverage uses isolated synthetic databases and no source constructors, private
source databases, model inference or network access.

### Session result filters

`GET /api/v1/graph/{revision}/sessions` lists real native-chat identities, with explicit stored-session fallback where native provenance is absent. `session_id` selects canonical assertion origin; `run_id` optionally chooses one recorded completed execution, and `output_snapshot_id` pins its causal-event watermark. These selectors are also retained in bookmarks. Flag `4` excludes an assertion from the session slice even when status history is enabled; canonical IDs, ordinal slots, geometry and global status evaluation remain intact.

Recorded overview results use the Core `run_provenance` read helpers at one watermark for output pages, causes and lifecycle. Finished outputs and referenced inputs retain exact `(view_id, version_id)` identities and qualified transport IDs. Compact mode combines the latest finished output per view and its exact prerequisites; selecting a run gives that run's outputs. A reused material version is distinguished from a newly produced one. Caller-context-only attribution is not authenticated source origin. Older uninstrumented history stays unknown; mutable last-job outputs and membership overlap are never causal-history fallbacks.

`topics` and overview cards contain only the selected pinned materials. `layout_topics` and `layout_memberships` carry the current full-family presentation basis solely to preserve visual coordinates. The manifest's `session_filter.overview_history` reports runs, the pinned watermark, pending/caller-context counts and unconfirmed historical coverage. Causal-event watermark changes invalidate live result projections even if canonical source state and material version IDs have not changed. Source reads remain query-only; this viewer does not initialize causal schemas, generate material or backfill missing provenance.
