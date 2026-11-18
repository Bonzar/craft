# Thin remote capture verification — 2026-11-16

TDD red: tests/test_assertion_memory_capture.py failed before the native reader and
outbox existed. Green: **219 tests passed, 22.457s**, Python 3.12 with the same
fixture PATH requirement as stage 1. Suites: module/capture/remote plus existing
installer, harness, mentions, skills, session bootstrap, Codex wrapper and startup
context suites. git diff --check passed.

New focused checks include:
- First user already persisted before startup, then future assistant capture.
- Unregistered resume starts after the current tail; registered resume retains cursor.
- Two device copies produce identical provider-backed public capture arguments.
- Repeated ID-less same text remains two durable occurrences, restart is idempotent.
- Paired user protocol proof; no RR ambiguity borrowing; nonhuman/unknown origins
  and inherited fork history excluded; unrelated session is never discovered.
- Only user, visible assistant and attributed tool output; no system/developer,
  analysis or tool invocation argument capture.
- Missing/partial source/header retry; oversized source becomes an explicit gap.
- Replaced/truncated or in-place proof-mutated source fails closed.
- Queued messages and cursor roll back together on immutable identity conflict.
- Lost remote ACK replays identical arguments; wrong receipt does not acknowledge.
- Native Codex http_headers_helper and Claude headersHelper read private token file.
- MCP initialize/initialized/tools-call, structuredContent and text-JSON responses.
- Transport errors do not echo token or remote body; disabled binding prevents send.

All fixtures were synthetic and temporary. No live endpoint, token, personal DB,
transcript, model, global config or installed module mirror was used or changed.

Boundaries still requiring live acceptance:
- Global actual startup hook execution and native MCP availability on each laptop.
- Real persisted native message shapes and remote durable source receipts.
- Source gaps/pending outbox are local: there is no remote session/gap-registration
  API. A remote memory query cannot see events not yet delivered to it.
- Provider-ID-free independent device copies cannot be safely deduplicated.
- The capture reader supports Codex JSONL; the independent MCP connection supports
  Codex and Claude. Unregistered forks start conservatively at their current tail,
  which can exclude input already written into the fork before registration.
- The worker is launched by startup hooks, not a login-level service supervisor.
