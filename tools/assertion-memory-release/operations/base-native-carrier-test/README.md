# TEST native Base owner carrier, v1 review candidate
This draft is source-only. It is deliberately not callable from the public app,
release workflow, or the installed SSH receiver. No startup, role, key, device
registry, DNS, Gateway, Sites, data migration, or PROD change is included.

Ordinary owner requests already resolve to the canonical TEST listener
127.0.0.1:8891. Keep the public URL, 8893 private Base listener, owner login,
database inode and owner-store inode, existing direct TEST ticket ingress, and
legacy unit bytes. Never proxy privately authorized machine requests through
the new browser app. Protected installer validates the loaded release
revision/archive separately from the queue release tag/generation. Free port
alone is not proof of retirement or approval.

The stock receiver only allows a signed TEST Base deploy and the installed
lifecycle hook is not controlled by the app payload. Remaining required:
existing authorized TEST lifecycle controller binds its actual admitted release
state (no new key or workflow permission) to this fixed handoff. The controller
must establish a bounded private read-only ASGI shadow with existing cookies,
verify real service/MainPID and exclusivity under the deployment lease, then
retire only legacy Graph, start native on 8891 in the existing Base process,
and verify native owner and private 8893. Use the actual versioned release
receiver/hook to do this, not browser calls, raw SSH, or unit modifications
from the app. This candidate ships no installed controller implementation.
A protected machine-level acceptance must confirm both the final installation
authority and correct identity for both listeners before live TEST activation.

No shared or real deletion is part of the handoff smoke. The repository tests
use temporary SQLite and in-memory HTTP/ASGI. They prove same login, graph
manifest/assets, queue pause/filter prepare, SSE epoch/ticket/reconnect,
and independent synthetic node action. Fake adapter tests prove retirement
ordering, inverse on failed smoke, rejecting stale/foreign leases, and explicit
inverse. They do not demonstrate native process launch or installed hook
permissions on the shared system. Avoid lock re-entry: any authenticated
read using the shared deployment lease occurs outside exclusive lock; internal
under-lock readiness uses the exact listener/read-only endpoints without that
lease. A service smoke failure must restore predecessor using the old
configuration and correct process ownership; proof ends only on re-read.

Run from repo root:
```sh
PYTHONPATH=tools/assertion-memory-release/operations/base-native-carrier-test:. \
python -m unittest -v test_native_test_carrier_install test_native_carrier_parity_local \
  test_native_test_node_binding_proposal local_test_381_startup_seam
```

## Existing hook boundary and separate operator review

Current Base archives deliver the application, not the protected host hook.
The ordinary receiver validates the immutable archive and invokes the already
installed `/etc/assertion-release/test/service-hook.sh` under its existing
TEST lease. Publication of the historical `base-browser-test-carrier-v0.1.1`
only reviewed Nginx/Gateway rendering and is not authorization to replace
this hook. Root provisioning installs it separately; default restart and
restore are a joint systemd restart without cross-unit ownership order.

Before proposing a hook operation, obtain read-only proof from an already
authorized operator: canonical TEST hook path, root ownership, mode, link count,
regular file and race-safe SHA-256; record exact running TEST tag, signed
revision/archive, journal absence, and effective identity of Graph and Base
unit files, drop-ins, MainPID/cgroup, and owners of just :8891 and :8893.
Inspect whether the canonical independent Sites read Gate is actually enabled
and use its installed verifier hash/status (not secret content). Preserve it.
Report only version and expected-vs-observed typed predicates. Do not infer a
host hash from a source blob, or infer service absence solely from a free port.

A separately approved immutable TEST hook package must pin old and new
hashes; maintain a reversible byte-for-byte previous hook, update only that
canonical regular file with unchanged ownership, under the existing TEST
deployment lock, and stop on drift. No new key, role, deployment channel or
public route; no PROD policy reuse. No host change may be made just by merging
this PR. Before permitting the app artifact, verify the independent hook
installation and its ordinary release/restore with the two actual units.
The source schedule and journal simulation do not implement the host adapter.

The ordered schedule tests additionally exercise the exact repository
activation journal on a real temporary filesystem:
```sh
PYTHONPATH=tools/assertion-memory-release/operations/base-native-carrier-test:. \
python -m unittest -v test_paired_hook_schedule
```
