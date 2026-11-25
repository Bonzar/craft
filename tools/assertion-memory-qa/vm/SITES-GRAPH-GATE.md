# TEST: make the Sites-facing Graph part of the Base release

Current TEST paths are different: the receiver installs/restarts its own
user-graph service at 8891 and MCP at 8893, while the actual Sites graph path
`/memory-graph-test-read` goes to the existing system unit
`memory-site-test-graph.service` at 8819. The existing unit has the same lane
UID and imports current Base sources, but without restart it remains on the
physical release loaded at process startup. A green local 8891 check is not
proof that Sites uses the new Base.

The candidate keeps existing native Site, HTTPS Gateway, auth, wrapper and
port 8819 unchanged. After one-time host review, the verified release
transaction restarts the same 8819 unit after Base. It accepts the activation
only when the existing authenticated TEST Graph Gateway returns the exact
active Base tag and canonical release generation for its queue projection.
Rollback restores the old pointer, restarts the same units and repeats the
same smoke on the restored release. With the gate enabled, the receiver
does not restart or use 8891 as a release success surrogate. Retire that
redundant local graph independently once its consumers are checked.

## Deploy prerequisites (no live changes applied)

1. Confirm the verified 8819 unit runs as `assertion-test-deploy` and that its
   Python imports from the lane's release `current`. Confirm exact current
   Gateway-to-unit route, existing dedicated root-managed TEST Graph credential
   file within canonical TEST state, deployment pin and the runtime projection
   configured in the same unit. Do not copy/download credentials or grant
   access to existing private config. If the Graph credential is stored
   elsewhere, update/review the adapter's exact path rule before enabling,
   rather than changing its permissions or letting callers supply arbitrary paths.
2. This gate requires the incoming release **and the retained rollback target**
   to support the live projection and exact Sites-compatible status contract.
   On the current existing runtime, prove positive Gateway status and
   negative missing/wrong credential without displaying response bodies.
   Prove restarting 8819 does not modify the Sites ACL, VM network or PROD.
3. Root reviews and installs exact committed versions of
   `test-service-hook.sh`, `test-health.py`, and `test-sites-graph-gate.py`
   into `/etc/assertion-release/test`. No remote build executes these as
   root and any code outside the reviewed hosted helper is untrusted.
   Keep the hook operator-owned and its modes pinned. The **opt-in** marker
   `sites-graph-gate.json` is root-owned 0644/single-link, with only
   `gateway_host` (existing exact TEST Gateway hostname) and `token_file`
   (reference to the existing canonical TEST Graph read credential, never raw
   token). Missing marker retains existing behavior; invalid marker fails closed.
4. Validate restricted systemd delegation for `assertion-test-deploy`:
   only the exact argv of `/usr/bin/systemctl restart memory-site-test-graph.service`
   and `/usr/bin/systemctl stop memory-site-test-graph.service`. No wildcard,
   arbitrary unit, shell, environment changes, edit/enable or PROD. Ensure unit
   fragments/drop-ins and executable wrappers are root-controlled, the unit starts
   unprivileged, and **no** privilege-inheriting commands use release-controlled
   inputs. Review effective sudo policy and denial for all other actions before
   enabling. The status check `systemctl is-active --quiet` doesn't require sudo.
5. Scope activation/rollback checks to the standard Base release workflow, under
   its existing release lease. Confirm old process cannot serve active generation
   after pointer swap (module binds physical release before following later
   symlinks), and confirm next release changes the proof. Do not mark
   TEST healthy if existing authenticated Gateway becomes unavailable.

The existing Sites V25 transport forwards URL query via the same read-only
POST envelope; it checks standard capabilities fields and intentionally reports
`capabilities.live=false`. It doesn't allow or transport SSE. The browser
dashboard's polling fallback is required until a separate release-managed
capability change proves time-separated SSE frames at the real owner Site.

## Distinct frontend release proof

This Base gate validates the actual *backend ingress*, not released frontend
HTML. The Visual release continues to own immutable frontend assets. A separate
owner-authenticated native Sites smoke must load the stable TEST entry URL,
verify it resolves to the exact expected Visual digest, and compare the served
index and JS hashes to that Visual archive. The VM asset-only smoke cannot
detect cached/bypassed native Site routing. Old pinned URLs intentionally retain
old bytes, so bookmarks for current UI use the stable entry URL.
