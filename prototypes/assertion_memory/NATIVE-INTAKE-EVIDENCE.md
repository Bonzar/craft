# Native intake adapter — synthetic verification

Task: automatically capture future raw native user and visible assistant context
from explicitly allowed transcript paths, then schedule the existing MCP worker.

RED checkpoint: `071468a` (`test: reproduce missing automatic native transcript intake`).
`python3.12 -m unittest prototypes.assertion_memory.native_intake_test` executed
and failed with `ModuleNotFoundError: prototypes.assertion_memory.native_intake`:
the new adapter API did not exist. No production changes preceded that checkpoint.

GREEN verification:

- `python3.12 -m compileall -q` for the adapter, tests, and changed fast adapter:
  PASS.
- `python3.12 -m unittest prototypes.assertion_memory.native_intake_test
  prototypes.assertion_memory.desktop_fast_test
  prototypes.assertion_memory.desktop_test
  prototypes.assertion_memory.tests.test_context_capture
  prototypes.assertion_memory.tests.test_native_acceptance
  prototypes.assertion_memory.tests.test_desktop_dialogue`: PASS, 73 tests, 7.379s.
- `git diff --check`: PASS.

The 14 new synthetic cases cover raw capture without hooks/models/recall or
embeddings, byte-position/source identity, whitespace, role truth, aliases,
cutoff/bootstrap, native proof pending/retry, denied origins/cwd/session, restart
and dedup, partial lines, future turns in a real daemon process, replacement,
truncation, changed bytes, explicit bounded gap, and cross-process lock ownership.
A worker-launch boundary test checks raw visibility from a separate DB connection
before scheduling. No live transcript, live graph, hook installation, or model
provider was accessed.

The unrelated `tests.test_desktop_bootstrap` import cannot run from this product
seed: its required `tools/jarvis-install` file is absent. There is no configured
standalone lint/typecheck/build script in the product seed; Python compilation
and relevant behavioral suites are the mechanical checks.

Limits: fixed explicit paths only; native rotation/replacement requires operator
source review. The current turn and restart backlog must fit within 2 MiB, with
an explicit fail-closed `IntakeGap` otherwise. No unbounded capture guarantee or
automatic silent skip. Profile/path authority changes require a separate cursor
chosen after source review. Installation/restart supervision is left to the caller.

## Review follow-up: idle probes and derived-only recovery

RED checkpoint: `31975a1`. Three new test targets executed and failed with seven
assertions: all five derived-only scheduler cases launched zero workers, an idle
poll constructed `MemoryStore`, and a NaN interval was accepted. Production code
was unchanged until that checkpoint.

GREEN: the same watcher suite now passes 17 tests. Both existing scheduler deadline
methods are reused without constructing an engine or provider. Idle polls still
read and validate bounded native proof, then skip SQLite and cursor writes until
source bytes change or the 30-second probe is due. Rewrites and truncation remain
fail-closed. Source-empty derived pending/waiting/running/retry/temporal work
schedules after the durable cursor; repeated polls remain throttled. Nonfinite
intervals are rejected.

Final verification: Python compilation PASS; the same six relevant modules above
PASS, **76 tests in 7.489s**; `git diff --check` PASS. Synthetic data only.
