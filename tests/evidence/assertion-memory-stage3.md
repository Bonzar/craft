# Capture fidelity corrections — 2026-11-16

Astra review exposed four stage-2 defects. Five exact synthetic reproductions
first failed, then passed: R/source timestamp versus I/completion timestamp;
provider-backed copies with an extra service line; explicit-human singleton R;
unverified native-client R lost on turn reset; fork input persisted before startup.

Corrections:
- Original R/source clock is authoritative; absent/invalid R time stays unknown.
  I corroborates content/occurrence and never supplies a fallback source clock.
- Public capture omits position, using the agreed optional-position API. Physical
  row offsets/line order remain local delivery observations, not immutable public
  identity or claimed session-global producer order. Existing saved outbox payloads
  and receipts are not rewritten.
- Explicit-human unambiguous singleton R is admitted at a confirmed downstream
  boundary. Native-client input still requires RI proof. Deferred/ambiguous input
  becomes a visible gap at turn end; pending proof is visible in local status.
- Startup forks are read from their header. Explicit current-session completion
  proof admits fresh input, while inherited parent-thread history is excluded.
  Missing fork provenance yields a source gap instead of silently tail-skipping it.
- Delayed I preserves the already-admitted R identity/clock without duplication.
  Human singleton I permits absent thread identity but rejects wrong present IDs;
  completion-only source time remains unknown.
- Atomic INSERT OR IGNORE registration prevents concurrent global/project hooks
  from replacing a worker's progressed cursor or gap state.

Green: **231 tests passed, 21.218s** (50 focused module/capture/remote tests plus
181 existing installer/harness/startup/wrapper tests), Python 3.12. diff --check
passed. All message data was synthetic; no personal base/transcript/token was used.

Additional actual local protocol check: bundled codex-cli 0.167.0-alpha.3.2,
app-server hooks/list with an isolated temporary CODEX_HOME and trusted synthetic
Git project returned BOTH user/global and project SessionStart hooks, with their
own sourcePath and ordered entries. No hooks or models ran. Thus installing both
layers can duplicate bounded policy text; they do not override each other. Shared
capture state and worker flock provide one cursor/outbox/worker. Evidence from the
read-only RPC is /private/tmp/memory-hook-layers-j37e1lwn/observed.json.

No global install, original source edits, personal profile widening or live
capture activation was performed. Remote source/frontier visibility is a separate
pending unit; stage 3 does not claim end-to-end readiness.
