# Local code delivery

`manual_release` is a standard-library helper for a **verified checkout or extracted
archive**. Its source commit is caller supplied; it does not authenticate the
checkout or archive. Use the final tested commit, with no uncommitted code changes.

```sh
python -m prototypes.assertion_memory.manual_release stage --source-root "$VERIFIED_CHECKOUT" --channel "$CHANNEL" --source-commit "$SOURCE_COMMIT"
python -m prototypes.assertion_memory.manual_release promote --channel "$CHANNEL" --release-id "$SOURCE_COMMIT"
python -m prototypes.assertion_memory.manual_release status --channel "$CHANNEL"
python -m prototypes.assertion_memory.manual_release rollback --channel "$CHANNEL"
```

Only Python code and requirements files inside `prototypes/assertion_memory` plus
an optional `prototypes/__init__.py` are copied. Hidden directories, tests,
`*_test.py`, caches, databases, JSON configuration, runtime environments and model
weights are excluded. Source symlinks are rejected. Staged code files and directories are read-only (ordinary imports cannot add caches);
existing commit directories are never overwritten. Every payload file is hashed
in `manifest.json`, and every promotion validates the entire payload.

After successful checks, configure request launchers separately to use `CHANNEL/current` as their engine root. This helper changes no connection
files and refuses to replace an existing `current` directory. `current` switches
via atomic symlink replacement; `previous` retains the old release, and
`receipt.json` records the last change. Release trees are never deleted.

New request processes see the new code. Existing workers retain loaded code and
need a separately verified restart by exact ownership; this helper inspects or
kills no processes. `release_info(engine_root)` returns explicit release metadata.
No database is opened, copied, migrated, reset or rebuilt. Rebuilding a new database
from an old graph is a separate, explicitly authorized source-replay operation;
derived interpretations must not become new human source facts.

Verification: `python -m unittest prototypes.assertion_memory.tests.test_manual_release`
uses temporary directories and synthetic bytes only. RED at `4e31cde`: missing new
API import; GREEN: six tests passed, including import layout, exclusions, tampering,
idempotence, rollback, symlink escape rejection and unchanged database bytes.
Follow-up RED at `a17a6e3`: ordinary Python imports added an unmanifested cache.
Follow-up GREEN: eight tests passed, including ordinary imports without cache
mutation and simulated failure of atomic current replacement.
