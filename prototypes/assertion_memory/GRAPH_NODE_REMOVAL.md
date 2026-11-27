# Owner: remove one graph thought
This private, loopback-only API uses the existing owner write credential and exact
path/lane/body signature. Each path has a distinct proof. Trusted Visual must
validate its browser owner session and CSRF before signing; do not forward a
browser-supplied identity. Neither route is public or mounted by Sites.

1. PUT `/api/v1/graph-nodes/remove-preview`:
   `{"thought_id":"thought-…","thought_version":3,"expected_revision":"00000000000000000000000000000000"}`.
   This performs no DB writes. It returns stage, thought_id/thought_version, scope
   (database generation), source_revision (canonical graph), and revision (the
   confirmation challenge). Display the selected item and ask for confirmation.
2. PUT `/api/v1/graph-nodes/remove` with the same stable thought ID and integer
   version, and challenge as expected_revision. Use a fresh proof nonce. A 200
   returns those same identity fields, removed=true, and the **new** canonical
   source_revision. Reconcile by refreshing the graph (Visual's own packed graph
   revision is not the canonical source revision). No optimistic removal on 409.

This withdraws exactly one **current** canonical head from the active graph and
search, including its visible incident edges. Other thoughts, stored exact
historical versions, audit links, evidence and original raw/chat content are not
deleted. No history/ordinal/raw/topic ID, force option, or cascading operation.

Expect typed 409 for changed/missing node, stale confirmation on graph/release or
database generation change, dependent retained thoughts, or unfinished work on
the selected node. No partial commit. Support for materially larger databases
requires an indexed dependency proof: this implementation deliberately refuses
a scan beyond 5,000 stored external versions instead of pretending safety.

## Visual integration
The authenticated owner browser uses POST `/api/v1/processing/graph-nodes/remove/prepare`
and PUT `/api/v1/processing/graph-nodes/remove`. The browser flow binds the returned
one-use confirmation to the same session, lane, thought ID, version, and revision.
The browser cannot reach the private Base route directly. Both lanes use the same
`GraphNodeOwnerBridge` over the pinned loopback Base port. The browser routes and
capability are absent unless a reviewed deployment explicitly injects a bridge
for that exact lane, and the ingress generator defaults off separately. No
credential/value changes, trusted route activation, or database mutation are
included in this candidate.

## Standalone TEST startup and ordinary existing ingress
The optional source flag `--browser-owner-graph-node-removal` defaults off. It
uses the existing synthetic TEST owner app at `127.0.0.1:8891` under the
`assertion-test-deploy-graph.service` **user manager**, not PID 1. Its service
credential directory is the systemd runtime credential prefix for the canonical
TEST account and exact unit; it is checked against the current process cgroup,
not selected by an environment path. See the [systemd credential docs](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#Credentials)
and the previous protected TEST activation receipt. No additional authority is
provisioned here. Explicit enablement fails closed when the canonical TEST unit,
credential provisioning or root-trusted Base fingerprints do not match.
Recognized reader and Base writer take shared nonblocking leases on the same
existing TEST deployment lock; PROD still uses its separate reader fence.

No ingress route additions are part of this patch. Before enablement, obtain a
read-only exact-match receipt for the following:
- `systemctl --user show assertion-test-deploy-graph.service`: verify unit ID,
  ControlGroup/MainPID, account, effective listener/argv and existing
  LoadCredential/LoadCredentialEncrypted/ImportCredential **names only**.
  Verify actual cgroup-v2 path of its MainPID and the resolved systemd credential
  directory/files by owner/mode/presence only. Do not print credential contents,
  full process environment, configuration secrets, or operators' HTTP headers.
- Managed TEST Gateway effective spec: both ordinary wildcard POST and PUT (or
  existing exact overrides) select `/__mvt/api/v1/{tail}` without stripping the
  path or body and preserve Cookie, Origin and Content-Type. Commit must preserve
  X-CSRF-Token. No copied owner headers or public Base port.
- Effective TEST Nginx location selection for the exact two node paths:
  rule permits each method, rewrites only the private prefix, forwards body and
  exact required owner-cookie headers to `127.0.0.1:8891`, not old :8829 or any
  legacy/Sites path. Check exact and regex overrides, not just the GET wildcard.
  Only if a real mismatch is observed should protected ingress be changed.

### Later TEST acceptance, disposable synthetic fixture only
1. Before enablement capability and exact browser routes are absent. After
   enablement, ordinary owner login advertises node remove for TEST; anonymous,
   wrong Origin, missing/invalid/replayed CSRF cannot mutate.
2. Prepare and commit one isolated current thought; verify authoritative new
   graph/search revision hides it, with raw source IDs, prior thought history
   and independent neighbors unchanged. No second commit on retry.
3. For a different seed referenced by another retained thought, require typed
   `dependent_thoughts` 409 (possibly at prepare) and unchanged revision,
   neighbors, raw and history. A changed version must require fresh review.
