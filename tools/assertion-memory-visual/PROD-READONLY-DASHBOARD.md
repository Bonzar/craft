# Release-managed PROD queue dashboard (read only)

This adds no mutations and changes no existing Graph/VM ingress, service
identity, DB mode or authorization. The ordinary published Graph behavior is
unchanged unless explicitly opted in. It is not the TEST mutation path, and
requires no TEST credentials or settings. No model generation, Monium, pause,
device enrollment, or reset is enabled here.

## Preconditions for the existing PROD reader

- The already serving root-managed systemd unit
  `assertion-memory-prod-site-graph.service` must be positively identified
  on the actual PROD Graph gateway and have an existing nonroot real and
  effective UID. Determine the account and exact UID from the actual service
  process; do not assume the Base deployment user's UID. Preserve its
  existing owner/read auth and ExecCondition transition guard.
- The serving Graph app must load its Python package from the physical
  release selected by `/srv/assertion-release/production/current`, and the
  actual Graph read path must already be the same canonical protected
  `/var/lib/assertion-memory/memory.sqlite3` inode as the live Base publisher.
  Verify at the actual service, not just a loopback substitute. If it's
  independently vendored or opens a different database/copy, do not force
  activation or loosen permissions.
- Provision only the existing-style PROD nonsecret publisher directory
  `/run/assertion-memory-dashboard/prod`. The publishing service owns the
  lock/projection, and the graph reader only needs read/traverse on the
  dedicated private group there. Preserve existing DB and private config
  permissions. The actual Base process, not Graph, publishes the minimal
  active scheduler projection.
- The existing root transition guard's directory/file
  `/run/assertion-base-prod-transition/fence.lock` must have the reviewed
  root-owned 0755/0644 modes and exact identity. This code only takes a shared,
  nonblocking read lease; it never creates a file or assumes an exclusive
  lease. Queue state is unavailable while an exclusive transition/reset holds
  the fence. The existing root ExecCondition remains in force.

## Explicit release configuration (not applied here)

After reviewing the exact serving identity, provision
`/etc/assertion-release/production/graph-dashboard-reader.json`
as root-owned 0644 regular single-link with the exact actor from the unit:

```json
{"stage":"prod","account":"<verified nonroot account>","uid":0}
```

Replace the illustrative `uid:0` with its independently verified nonzero
numeric UID; zero is explicitly rejected. Account lookup must give exactly
that UID, and the process real and effective UIDs must both match.
Only two nonsecret env vars are needed in the existing PROD Graph unit:

```ini
[Service]
Environment=ASSERTION_MEMORY_QUEUE_DASHBOARD_STAGE=prod
Environment=ASSERTION_MEMORY_QUEUE_DASHBOARD_PROJECTION=/run/assertion-memory-dashboard/prod/scheduler.json
```

The published projection is accepted only if stage, live publisher file lock,
Base generation and DB device/inode all agree with the monitored release.
The reader resolves the physical code origin at import rather than following a
later change to the `current` symlink. Failures or missing setup leave the queue
unavailable and do not change ordinary Graph requests.

Roll out only through the approved Base/Visual release chain, preserving the
existing transition mechanism. The exact owner-authenticated production Site
must return `stage:prod` and the activated Base generation from its existing
`/api/v1/capabilities?queue_status=1` read path, with the same Visual stable
entry resolving to its own immutable asset digest. Test denial for missing
authorization and wrong origin; do not inspect or log queued message content.
The current published V25-style native graph transport may poll rather than
stream. Verify actual live forwarding before claiming updates work. No
write, cleanup, or restart is part of this change.
