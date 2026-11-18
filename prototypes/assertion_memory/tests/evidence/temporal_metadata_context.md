# Redundant temporal clock citation regression

Synthetic-only fix based on the C8 representation failure: an exact body anchor
(`Yesterday`) was accompanied by the same source's `recorded_at` value quoted as
if it occurred in the body. No private records, deployment, or real model calls
were used for these checks.

## TDD checkpoints

Task: remove only the exact redundant clock citation, after validating actual
same-source body evidence, while retaining independent date/meaning verification.

- Regression target: `test_temporal_metadata_context.py` (10 tests).
- RED commit: `04198966fcc86c21f7d10ce053723aaef3c52068`, before production changes.
- RED run: 10 tests; 2 failures and 5 errors. Paired exact clock citations raised
  `ModelError: evidence quote is absent from its source event`; the public service
  worker left `error_count=1`, matching the reported failure.
- GREEN run after the fix: the same 10 tests passed.
- Related suites: 72 tests passed (new regression, temporal, dialogue context,
  context recovery, repair, repair status, source meaning).
- Full prototype discovery: 310 tests passed in 63.546 seconds, including fixture
  subprocess/SDK tests. No production source edits occurred during the run.
- Python compilation and `git diff --check`: passed. This seed has no configured
  type checker or linter. No build applies to this Python logic change.

Commands used the existing installed runtime Python and this isolated worktree:

```sh
python -m unittest prototypes.assertion_memory.tests.test_temporal_metadata_context -v
python -m unittest prototypes.assertion_memory.tests.test_temporal_metadata_context prototypes.assertion_memory.tests.test_dialogue_temporal prototypes.assertion_memory.tests.test_dialogue_context prototypes.assertion_memory.tests.test_dialogue_recovery prototypes.assertion_memory.tests.test_dialogue_repair prototypes.assertion_memory.tests.test_dialogue_repair_status prototypes.assertion_memory.tests.test_source_meaning -v
python -m unittest discover -s prototypes/assertion_memory/tests -p 'test_*.py'
python -m compileall -q prototypes/assertion_memory/dialogue.py prototypes/assertion_memory/tests/test_temporal_metadata_context.py
git diff --check
```

## Guard coverage and evidence limits

The tests cover the six design cases: paired body/clock normalization; metadata
alone and wrong-source pairing; fabricated/approximate quotes and explicit start
(including null); foreign, unauthorized, unavailable and unsupplied source spans;
real timestamp literals retained as ordinary body references; and propagation of
independent rejection for wrong dates, ambiguous timing and wrong attribution.
A bad later reference or claim prevents the normalization audit from committing.

The public service capture/worker/status/source path preserves both radio and
undated harbor meanings, resolves the compact temporal proof to real `Yesterday`
text, records normalization in immutable extraction history, and leaves raw data
unchanged. No timestamp body span is fabricated. The audit records operation,
source event ID, field, count and SHA-256 of the exact source metadata value.

Provider fixtures check boundary payloads, independent-verdict routing and source
coverage orchestration. They do **not** prove real-model temporal entailment or
recovery of every malformed response. A fresh fictional real-model C8 acceptance
and independent semantic/date review remain separate release gates.
