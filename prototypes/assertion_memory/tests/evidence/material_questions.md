# Material attributed questions

Synthetic contract/pipeline evidence; no real model, personal data or production writes.

RED: `python -m unittest prototypes.assertion_memory.tests.test_material_questions -v`
ran 9 tests: 7 failed because existing blanket question exclusions caused accepted-empty
extraction and source review; routine raw-only and legacy literal guard passed.
Exact output: `material_questions_red.log`. Production files unchanged at RED checkpoint.

The scripted extractor obeys existing blanket exclusion clauses. A separate verifier
checks a bounded allowlist of attributed interpretations AND exact supplied source
spans, rejecting premise, choice, assignment and global-open-state promotion. This
proves service/worker persistence, provenance, protocol and automatic view wiring;
it does not prove semantic model compliance. Independent real semantic review is
still required before release.

GREEN: final focused suite ran 13 tests, all passed (`material_questions_green.log`).
Final harness replayed unchanged against detached baseline 210a18d: 13 ran, 9
failed as expected (`material_questions_final_baseline_red.log`), including 8
behavioral pipeline cases and 1 supporting request-contract parity case. The
capacity investigation, routine raw-only, legacy literal guard, and independent
reviewer negative control pass even on baseline. This more precise final replay
limits exclusion emulation to interrogative/reported-question source material;
the initial RED fixture also suppressed the declarative capacity variant.

Fixture corrections discovered after first GREEN attempt: persisted reference
quotes are deliberately compacted, so check exact excerpts and resolve source
spans through the real queue; historical recorded cutoff must be the completed
snapshot capture time (source recorded_at predates interpretation availability);
rejected repairs consume the existing shared two-round budget, so negative
promotion leaves source status unresolved. No production change was added to
accommodate these fixture assumptions.

Acceptance covered: four design cases; question-only project context; exact
source spans/actor; existing generic assertion kind; automatic relevant root
with no manual nodes or links; independent verifier rejects premise, decision,
assignment and global-open-state promotion; exact-content derived reviewer also
rejects invented current indecision; accepted-empty omission repair; old accepted
v1 meaningful and routine raw-only sources each receive one automatic v2 audit,
old history stays byte-equivalent, repeated idle ticks make no calls. Historical
speech act and later choice survive together; a frozen old snapshot and explicit
recorded cutoff omit the later choice. Request transport parity is supporting
contract evidence only.

Scope: only dialogue.py prompt contracts and source_coverage.py prompt/PROTOCOL
changed. SCHEMA remains source-interpretation/v1. Legacy guard, assistant context
scheduling, queue, worker, selection/ownership, temporal provenance and 32/6/2
budgets remain unchanged. No actual models, private sources, Craft or deployment.


Final verification:

- Focused material-question suite: PASS 13 (0.202s), including exact same-source
  vendor premise/causality/adoption/wrong-speaker rejection. Observational spies
  show the single canonical insertion carries its source event and independent
  verification; no manual node input or append_edge call, and zero persisted links.
- Related source/dialogue/worker/temporal suites plus root storage/model suites:
  PASS 133 (1.210s), exact output `material_questions_related.log`.
- Full prototype discovery: PASS 378, exact output `material_questions_full.log`.
  The final observational insertion/link spies were added after discovery had
  imported the suite, then verified in the focused 13-case run; no production
  edits occurred after the first GREEN checkpoint.
- py_compile of changed production/test files and git diff --check: PASS.
- No separately configured typechecker/linter/build applies to this pure Python
  prompt-contract edit; no actual-model semantic gate was run in this task.

Commands used the existing isolated Python runtime at
`Agent/prototypes/assertion_memory/.local/runtime/bin/python`:
`-m unittest prototypes.assertion_memory.tests.test_material_questions -v`,
`-m unittest discover -s prototypes/assertion_memory/tests -p 'test_*.py' -v`,
and named related suites listed in the related log. All data is invented.
