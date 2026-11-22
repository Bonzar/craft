# Assertion Memory: cloud CI and isolated QA delivery

**Scope.** Synthetic QA only. It does not use the Mac, personal DB, active QA
preview, existing MCP/Graph processes, any public-facing proxy, or any model.
There is no deployment to main or production. All embedded records in tests are
fictional; runtime fixture and secrets are generated locally and never uploaded.

## GitHub-hosted CI

Push the reviewed branch `codex/assertion-memory-ci-qa` (or open a PR). The workflow
uses GitHub-hosted Linux runners with Node 24, Python 3.12, the upstream npm lockfile
and pinned Python requirements. It builds the Graph, runs Graph and Python tests
for the documented offline contracts and backup/restore, and exercises original authenticated MCP and Graph against a freshly generated
fictional DB. Full semantic/vector fidelity and provider execution are explicitly
not part of this lexical QA.

The build publishes a versioned data-free tarball plus SHA-256, with its commit
and an internal payload checksum manifest. Only the two runtime source subtrees,
the built Graph assets and explicit QA helpers enter the artifact. No virtualenv,
working tree, private configuration, database or auth secret goes in the artifact.

## Gated operator pull and activation

No deployment runner, SSH key or root credentials are stored in GitHub. The
workflow does not deploy a release merely because CI passes; in the absence of a
provisioned dedicated account this is deliberately **manual gated CD**, not
automatic deployment to the VM.

1. In GitHub create the `assertion-memory-qa` environment; restrict deployments
   to the exact reviewed branch `codex/assertion-memory-ci-qa`, require a reviewer
   where supported, and set the environment variable
   `ASSERTION_MEMORY_QA_ENABLED=true` only after provisioning. Leave it unset until
   the dedicated host and user are ready. Run **Assertion Memory QA** manually on
   that branch and verify that both **build** and **Approve pull to isolated QA**
   actually succeeded for the same full SHA. Do not deploy PR artifacts.
2. An authorized system administrator makes a dedicated Unix account named
   `assertion-qa` on the intended QA host (or alongside other services with its own
   home directory), with no sudo, no memberships granting production access, and
   a persistent user systemd manager if wanted. No changes to existing service
   units, proxy, addresses, firewall rules or ports 8801/8803. Verify ports
   8891/8893 are free before provisioning; both new services listen on loopback.
   Configure QA access through an already authorized channel if needed, not a
   public reverse proxy. Use the pinned host identity when transferring artifacts.
3. On the trusted QA host, place the two reviewed `*.service` files under the
   dedicated account's `~/.config/systemd/user/`, and run
   `systemctl --user daemon-reload` **as assertion-qa**. Do not enable the units
   until the first release passes. Keep a trusted copy of `deploy-local.sh` and
   `verify-unpack.py` from the reviewed checkout on the QA host: don't execute
   an installer downloaded from an unreviewed build.
4. Download the approved run's archive and checksum privately. From the QA host's
   verified `assertion-qa` session (Python 3.12+, venv, SQLite FTS5), run
   `./deploy-local.sh /absolute/path/assertion-memory-qa-<SHA>.tar.gz <archive-SHA256> <full-commit-SHA>`.
   This validates the independently obtained checksum, profile and internal
   manifest before running anything. It creates a fresh synthetic fixture, makes
   new per-release test-only credentials, proves in-process and live Graph/MCP
   checks, and only keeps the new active pointer after those checks pass.
5. If the live check fails it stops the new QA processes and returns the
   `current` pointer to the previous QA revision. Failure on the first release
   leaves no active target. The previous release and fictional state remain
   in `releases/`; no production unit or datastore is read or touched.
   Review failure output from the new release's private directory; do not paste
   any credential files. If the systemd manager itself fails, inspect it locally
   before retrying; the old release files remain intact. Old versions can be
   explicitly pruned after validation; never delete the active or rollback target.

A future automatic deploy may use a narrowly scoped artifact-pull identity on the
dedicated tenant; do not install a general self-hosted GitHub runner against
untrusted PR code on the user's VM.
