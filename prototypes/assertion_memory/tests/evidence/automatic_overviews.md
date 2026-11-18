# Automatic overview regression evidence

These are public orchestration/protocol tests with fictional data. The deterministic
subprocess fixture is not an assessment of LLM semantic quality. The official MCP
SDK starts the actual stdio server, which supervises its independent background
worker. The test caller permits only capture, status and history; it never queries,
processes, defines, builds or refreshes a view.

Baseline: `6fe5a52`; isolated worktree: `/private/tmp/assertion-v8-worktrees/background-quality`.
Runtime: the supplied Python 3.12 environment with official `mcp==1.30.0`.
Command: `python -m unittest prototypes.assertion_memory.tests.test_automatic_overviews -v`.

| Requirement | Executed baseline evidence | Acceptance coverage |
| --- | --- | --- |
| R24 | [RED](R24-baseline-red.log), one actual SDK failure, source count 3, errors 0, `NO_INITIAL_OVERVIEW` | Capture alone, worker launch receipt, multiple actors/sessions, conditions/citations, real accepted versions/checks/frontier/history |
| R25 | [RED](R24-R29-baseline-red.log), missing initial automatic overview | Later public clarification, changed accepted material, immutable prior root, unchanged unrelated branch/version and no writer call |
| R27 | [RED](R27-R30-baseline-red.log), automatic root writer never reached | Exact owned worker/provider PID kill after a stored accepted leaf; restart/recovery and no repeat of completed full provider prompts |
| R28 | [RED](R24-R29-baseline-red.log), missing initial automatic overview | Cross-session semantic merge, same-session unrelated subject separation, one canonical source shared by two themes |
| R29 | [RED](R24-R29-baseline-red.log), missing public waiting-model descriptor; [existing assistant invariant PASS](R29-assistant-baseline.log) | Durable raw sources with no configured model, no false canonical/accepted overview, automatic configured restart, assistant-only context not promoted |
| R30 | [RED](R27-R30-baseline-red.log), first automatic discovery never reached | New real capture during paused frozen discovery; retained old generation, actual timestamp ordering, no later/private-scope source in historical root |

The initial RED run completed extraction, independent interpretation verification and
link proposal through the real provider before failing for the intended absent
initial overview. There were no import, SDK, provider-JSON or teardown failures in
the committed RED logs. Later tests extend the same regression gate; a RED before
initial overview necessarily cannot exercise their downstream assertions yet.

## Coverage still requiring separate evidence

R26 full 9000-record public capture/steady-ingest fairness and separately measured
CPU corpus scans are not exercised by this file. Existing private route discovery
scale tests do not substitute for that public end-to-end scenario.

R27 does not yet kill at all three specified checkpoints: it targets the accepted
leaf before root publication. R30 does not cover future valid-time eligibility,
expiration or an authorized cross-scope prerequisite; those require separate worker
and temporal tests. No pass is claimed for these missing cases.

Combined production commit `84af55f` was merged into this isolated test tree.
[Initial R24 GREEN](R24-combined-green.log): one actual SDK test passed in 2.387s.
[Final seven-test GREEN](R24-R30-combined-green.log): seven passed in 21.000s.
[Compact public proof](R24-R30-combined-green.json) records actual capture receipts,
worker/provider PIDs, accepted status descriptors, summary pages and provider task
counts. All tracked owned subprocesses exited; none remain. Full synthetic public
traces are retained under `/private/tmp/assertion-v8-sdk-green-proof` with SHA256
digests in the compact proof. Reproduce with `AUTOMATIC_OVERVIEW_EVIDENCE_DIR` set.

Confirmed identity contract: `root_request_id` remains stable per scope/policy/
pipeline. Materialized `root_view_id` may change with immutable dependency
definitions. R25/R30 use public `memory_history(current_root, section='routes')`
to follow every old root and its pinned versions; historical bytes, old source
frontier and private-scope exclusions remain asserted.

Only test/fixture/evidence files were edited by this testing agent. Production
changes in this tree came solely from the authorized integration merge.
