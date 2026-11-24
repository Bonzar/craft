# Operator/receiver boundary prototype (not for direct installation)

This source-only candidate is not linked from any release workflow. Linux/systemd
host tests and independent review are required before installing it. It changes
neither release authentication nor the immutable artifact. There are no credentials.

* The operator prepares an exact candidate Nginx config, freezes writes and
  verifies the only legacy holders are the known base/graph/adapter units.
  Arm the transient local watchdog before draining. The transition holds a
  shared kernel lock, verifies the physical DB inode, stops the legacy services,
  and publishes a short-lived boot-local permit with generation and inode.
* The authorized release service hook invokes only
  `receiver-first-start` on the installed root-owned guard for this first
  activation. It runs as the deployment UID; it is not sudo and never invokes
  a root shell or artifact as root. It holds the same local lock across
  permit/deadline/inode checks, startup and the postcheck. A second ordinary
  unfenced hook route to start the same first-release units is forbidden.
* Custody enters only after the old services stop and both descriptor and
  mapping checks report no holder. The guard only changes UID/GID of the exact
  confirmed-directory inode and canonical database plus currently present WAL
  and SHM, opened without following links. Modes stay 0700/0600; extra ACLs
  fail closed. The canonical directory/database device+inode are recorded
  before draining. A later recreated sidecar under its exact fixed name is
  eligible on rollback; a concurrent path substitution is not. Revocation
  and stopping the new base and adapter precede reverse custody. No copies,
  SQLite checkpoint, unlink, recursion, group expansion or ACL grants occur.
* The new adapter is a separate operator-provisioned readonly service with
  isolated writable cache and no write access to the canonical DB directory.
  Stop the legacy adapter before starting it. Confirm the new adapter,
  then switch exactly the two validated loopback upstream lines. Never change
  other routes; never repoint the graph to TEST. Verify authenticated MCP
  initialize/tools/read and the exact new Sites gateway behavior without real
  payload export before settling. The operator unfreezes writes only last.
* The watchdog revokes the permit before stopping new and restoring legacy.
  It refuses to overwrite unknown nginx bytes or to start old against a
  replaced SQLite inode. Hold a separate recovery console. The watchdog
  survives an agent/receiver failure but not a VM reboot; journal remains on
  disk. The pre-activation/receiver configuration and authenticated endpoint
  smoke are deliberate integration obligations, not implemented by Web/CI here.

Run `python3 -B test_guard.py` for mock local tests. These do not validate host
unit dependencies, ingress configuration, cutover fencing integration or a
live rollback. No installation commands should be run as part of code review.

## Sterile Linux ownership round-trip

Run `python3 -B test_linux_custody.py` **only inside a disposable Linux
container or VM as root**. It owns a newly created temporary fixture only;
it does not install a service, invoke systemd, mutate Nginx, or read any real
SQLite file. The test aborts if proc inspection is restricted. It exercises
descriptor-based exact UID/GID transition and restore and refusal while a
separate process holds the fixture. Real Linux/systemd watchdog, external
authenticated readback and projected per-service control restrictions still
require separate integration checks.
