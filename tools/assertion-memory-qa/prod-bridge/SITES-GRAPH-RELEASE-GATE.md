# Existing PROD Sites Graph: release guard candidate

No live hook is installed. This is a root-reviewed helper for the existing
production transition controller, not a CLI, a new privilege or a second
service. It intentionally has no direct way to restart a host. It is
independent from TEST pause #275 and never adds a write/MCP method.

## Why the existing release receipt isn't enough

The observed owner Sites Graph routes through the exact existing
`assertion-memory-prod-site-graph.service` wrapper. Its process identity
matches the deployment account, but the physical package imported by the
serving Python process did not come from the active Base release and the
process remained running through the release. Therefore matching a tag on
the Base HTTP listener, Graph asset host or a local alternative service does
not prove that the actual canonical Graph is on that release.

## Integration contract

1. **Review once, don't change the wrapper.** Stage only the generated
   nonsecret `drop_in()` for the existing service. It selects the active
   Base release import path and the scoped read-only dashboard options. It
   has no ExecStart, user, ACL, bind, guard, read-only DB mount, tokens or
   security edits. Check the actual unit plus drop-in and positively identify
   the exact unprivileged Graph account and its effective UID. Existing
   dependencies must import the released app under that UID in a read-only
   verification process without starting servers or opening the database.
   If not, qualify the existing release-managed venv as a separate source
   binding; do not add fallback searches into historical vendor paths.
2. **Existing authorized receiver owns receipt and seriality.** After exact
   artifact authorization and unpack, the reviewed production controller
   calls `prepare(candidate, rollback)`. The helper recomputes the Site Graph
   release generation using signed artifact name + checked manifest and
   captures one exact canonical DB device/inode (without SQLite read/write).
   The currently active rollback artifact must be available and importable.
   Treat lack of support in either version as a blocker, not degraded success.
3. **Call after actual root settlement.** Preserve canonical DB/inode, root
   transition guard and all authorization. Only once the existing production
   controller has independently reached its verified clean settled state and
   the existing fence permits shared reads, call `after_settle(expected)`.
   Supply a transaction-level serialization callback independent of the
   dashboard fence: do not hold the latter exclusively through a queue smoke;
   don't invoke restart in an ExecCondition-maintenance state. Host callback
   delegates only the exact existing Graph service restart by its existing
   trusted root controller, not arbitrary systemctl to the deploy user. No
   inspection of unbounded command output or credentials is needed.
4. **Two independent proofs.** Verify serving process real/effective UID,
   actual canonical physical source and same guard/inode, then query the
   existing authenticated read Graph Gateway with
   `POST /memory-graph-read`, body
   `{"path":"/api/v1/capabilities?queue_status=1&limit=1&offset=0"}`.
   Use only independently existing approved read authority; do not share
   Site credentials, read a token out of a running process, invent a second
   endpoint, or attach raw status bodies to the release. If only verified
   owner Sites browser authority is available, use its scoped authenticated
   release smoke, not a forged platform identity/header. Accept exactly
   the expected PROD generation and scoped read-only queues, not a green
   Base smoke. Probe actual native owner Site as separate acceptance.
5. **Failure and exact rollback.** When the proof fails, existing production
   transition logic owns the rollback. It restores the retained authorized
   release and settles the original canonical inode. Run the same callback on
   the restored release, with its previously checked identity. Never force
   restart on an undefined current or publish screenshots/bodies/logs from
   the owner's real queues.

## First migration must be staged across releases

A rollback target predating #274 cannot report the generation/read dashboard
from that release. First carry #274 through ordinary releases **dark** and
retain a verified rollback release with the required support. Only then arm
the receiver callback for a subsequent normal protected PROD release. No
shortcut that “assumes the previous release is close enough.”

Exact remaining host evidence before installing this helper:
- the reviewed production control code and authoritative settled proof
  schema/callback, including how it guarantees old/new root controller
  serialization, released transition fence, and same canonical inode;
- the effective current Graph unit's exact ExecCondition and chosen dependency
  interpreter under verified UID, no in-wrapper source override;
- actual existing approved read-only Gateway credential *availability*
  (never transmit the value to a chat or workflow log), or a native owner
  smoke channel the release controller can verify without inventing identity.

This source has no configured import or production policy change. It must
stay disconnected until the existing root controller is identified and
wired through an approved release. The current release's green Base receipts
alone do not constitute that evidence.
