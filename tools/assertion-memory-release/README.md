# Shared release mechanism

One source for qualification of assembled bytes, strict pinned SSH transport,
stream/hash/tar validation, staging, smoke, activation journal and rollback.
The base and visual build modes may still assemble different artifact types;
their exact archive hashes are unchanged by qualification. Product adapters
provide allowlisted manifest validation, a release identity, and a service or
static-origin hook. The workflow and VM environment decide which user may
invoke an adapter, never the incoming payload.

The TEST base entrypoint and already-merged inert PROD entrypoint now call
the same base manifest/hook adapter and shared engine. Visual can call this
engine with its own manifest policy while retaining its exact four-file QA
tar. A product or lane cannot supply the root directory, its executable hook,
its deployment UNIX user, its host pin or an alternate current pointer. The
shared core is not an authentication/approval API: PROD token checks, scope,
separate SSH credentials and immutable TEST proof remain mandatory policy
before entering it.

## Coordinated rollout, not done here

Install audited modules once as root-owned files at
`/etc/assertion-release/shared`. All Python `-I` receiver entrypoints use
that fixed path, never PATH/PYTHONPATH from SSH. Before adopting on the
existing VM, independently verify dedicated receiver account, host key and
fixed supervisor credentials; compare current TEST receipt and pointer
layout, journal, root namespace, permissions and actual read-only visual
group. Root installation must be atomic as a whole source version rather
than in-place writes to individual active modules. Keep exact previous root
owned package and TEST entrypoint for rollback; never rewrite personal state.
No generic SSH or widened gate. TEST rollback must smoke the previous
version, visual must confirm exact content from its origin, and a first
failed release must leave no current. Do not treat an external post-activate
HTTPS check as covered by in-transaction rollback.

The reusable Phase A source remains uncalled. Live TEST receipt for the
retained base release, live visual smoke, protected OIDC claims and PROD
activation remain independent verifications. No enabling flags or deployment
have been performed by this candidate.

### Already installed base TEST

Do not rerun the historical fresh provisioner to adopt the current TEST.
Use the dedicated first-adoption helper's read-only `--check`: it matches
the reviewed current receiver and forced wrapper, allowed existing UID/home,
single lock, root-managed key/hook and valid current marker. Stage only an
independently approved root-owned exact package of `receive.py`,
`base_payload.py`, `release_core.py` and build manifest `SHA256SUMS`.
Supply independently verified manifest and old wrapper digests.
A later allowed `--apply` with a second verified admin session atomically
publishes the shared source and then the TEST entrypoint, probes denials,
and keeps the exact old installed entrypoint for rollback. It never edits
SSH config/keys, the existing account, service units, state or ports. This
first migration rejects a different preexisting shared package rather than
replacing it in place.
