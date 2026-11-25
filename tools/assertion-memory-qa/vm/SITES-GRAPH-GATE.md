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
same smoke on the restored release. Keep restarting both existing TEST user
units initially so no still-configured service can use an obsolete runtime.
The 8891 service is not used as a release success surrogate; actual 8819 is
still required. Retire redundant 8891 separately after proving its consumers.

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


## Make the running 8819 generation unambiguous

Review a drop-in for the same existing 8819 unit. Preserve User/Group,
WorkingDirectory, host/port, credential environment, unit guards, and
all current runtime/auth inputs. After checking its existing command has
no additional arguments, provision the reviewed launcher root-owned 0755 at
`/etc/assertion-release/test/test-sites-graph-launch.py`, then override
only ExecStart:

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/python3 -I -B /etc/assertion-release/test/test-sites-graph-launch.py
```

It starts the same root-pinned `/opt/assertion-memory-site-test-graph/start.py`
as the unprivileged TEST user, but invokes the interpreter via the physical
current release and sets PYTHONPATH to that same immutable directory.
No shell, credentials, new ingress, or permission changes. Never add
runtime arguments not confirmed from the exact existing unit; if the
current service cannot match this exact command shape, stop for review.
The two user service launchers must also expose their physical loaded
release via cwd, argv or PYTHONPATH. Verify that from actual live units,
then verify direct TEST Gateway queue identity on approved upgrade **and**
restoration before arming retention. No manual service restart/pruning
substitute for a green release and existing owner Sites read acceptance.

## TEST wrapper that stores only the Sites key fingerprint

If the real VM wrapper has only a fingerprint of the existing Sites route key,
do not copy that key to the VM or substitute an internal bridge/owner-local
credential. A distinct, root-enabled marker shape is available:

```json
{"version":1,"mode":"local_candidate_owner_pending"}
```

It qualifies the **local candidate**, not the user-observed delivery. It
asserts the real 8819 systemd MainPID, TEST Unix UID, exact immutable venv
command and PYTHONPATH, exact existing stage/projection environment, PID
ownership of the sole TCP :8819 listen socket across IPv4/IPv6 in its network
namespace (which must be 127.0.0.1:8819), and an authentication
denial on the actual local wrapper with no credentials. Then, as the
already-authorized TEST Unix service account, it uses the same released
Graph queue dashboard read method and canonical DB/projection paths to
verify the release generation and three queue IDs. The probe emits no DB
records and doesn't assert an HTTP success from an unauthenticated request.
It fails on a restarted process, stale generation, missing projection,
wrong listener, missing denial, or unavailable Linux proc data. Hook is
same for candidate and rollback restores, leaving native Sites auth untouched.

The test release's local qualification explicitly reports
`owner Sites acceptance still pending`. Before declaring delivered, using
the established logged-in owner browser, GET the existing TEST Sites
`/api/v1/capabilities?queue_status=1&limit=1&offset=0`; it must return HTTP 200,
stage TEST, the exact release tag/generation independently derived from the
approved release and the three actual queue names. Verify visible Queues
on the same stable visual entry. A login/error page, old generation, empty
projection, or unverified owner browser is a failed/pending end-to-end
check: do not promote, prune, or claim Sites operational from a green local
check alone. If organizational policy requires the Sites proof *inside*
the activation transaction, don't enable local-candidate mode: use an
independent already-authorized owner-side check (never raw key on VM)
and keep acceptance pending until that is wired.
