# One active Base build per lane

The Base TEST and approved PROD receivers share one transaction: lock, qualify the
candidate, record rollback, restart, smoke, and then commit. The old build must
exist until that commit. If smoke fails, restore and smoke the old build before
considering any cleanup. To roll back later, deploy the immutable old GitHub tag
again via the normal approval path; the receiver qualifies it from scratch.
Prevent implicit old state loss: separately require rollback compatibility for
schema/data migrations. This changes neither the database nor backups/secrets.

The optional `prove_inactive(active, inactive)` callback is intentionally not wired
into a live host. The same root-reviewed lane authority used for activation must
bind the callback under the existing lock. It may approve just the exact direct
child of the lane release directory after verifying all these conditions:

- The active generation passed normal service checks, the release fence and
  rollback journal are settled, and the active path remains unchanged.
- All lane services that can use packaged modules, including the existing
  independently managed Sites Graph process, are inventoried and qualified on
  the active build or pinned to a verified path outside the candidate.
- No worker/child process, open file, interpreter, cwd, live mapping, supervised
  future restart, stale wrapper, or mount uses the inactive tree. Process tests
  must use host visibility and a complete supervised service inventory, not a
  partial same-user /proc enumeration. Inability to prove this is a refusal.
- No alias, bind mount, or externally-managed/storage subtree exists in the
  tree; all writable database, config, credentials, artifacts and state are
  outside releases. Python venv symlinks are expected but are only unlinked by
  the FD-relative recursion, never followed.

Only a normal built Base name with prepared marker and correct owner/device is
eligible. Unknown/unprepared directories (including historical provisioner
trees) are deliberately not guessed. Failed first deployment with no verified
active release also retains its files. Before enabling, explicitly inventory
these leftovers under the same lane lock and report exceptions; do not run an
unguarded retention sweep. Unexpected identity, unavailable process proof or an
I/O error retains leftovers and emits a bounded diagnostic; never fabricate a
rollback after a committed healthy activation. A future deploy retries eligible
leftovers with fresh proof. Visual uses no retention callback.

## First rollout inventory and reconciliation

In each lane separately, run `inventory_base_releases(root, active)` under the
same existing deploy mutex, with the active pointer verified from the settled
guard. This is a dry run: report the active, eligible (pending host proof),
unprepared, unknown and unsafe counts. Even a proof-approved sweep may leave
unknown/unsafe entries; `current_only` is false in that case. It covers the
release directory, not the whole machine; do not claim server-wide
current-only until the external runtime inventory has also been verified.

Before activation, cross-check the exact already-serving Sites Graph process,
systemd unit+fragment identity, process cwd, imports and interpreter binding.
PROD currently has a separate fixed wrapper under
`/opt/assertion-memory-prod-site-graph` and independently installed runtime
source may reside under `/opt`. Find it from explicit unit configuration and
live process evidence, not directory naming. First point the **same** unit
(with existing guard and unchanged DB/path/auth) at the current artifact using
reviewed release-controlled configuration, qualify the existing owner Sites
read entry point, and prove restart binding. Only then separately classify the
old executable/source tree for explicitly reviewed retirement. The automatic
Base sweep never touches `/opt` or these historical directories. Keep a minimal
external wrapper/config if still referenced or required; it is not a second app
build and must not vendor application code after canonical migration.

Crash before commit: existing journal restores and smokes the prior release
before any pruning. Crash after commit: the current symlink stays committed;
next normal deployment reconciles prepared eligible noncurrent leftovers
under the same proof/lock. Interrupted filesystem removal may leave an
unprepared residue; classify and report it rather than guessing. A later
operator can reconcile only that exact residue after separate provenance,
inactivity and mount verification; no broad age/name sweep.

### TEST receiver wiring (default off)

The real TEST Base receiver reaches the shared Base payload path automatically.
It only binds the scanner if the root-created empty regular 0644
`/etc/assertion-release/test/enable-host-retention-v1` exists. No test
provisioner creates it. PROD never looks for this flag or socket. Install and
review the root scanner **before** creating the TEST marker. No old release
is deleted when the socket/proof is missing.

The dedicated local scanner protocol uses
`/run/assertion-base-test-retention/authority.sock` in a root-owned
non-writable-to-clients directory, with connect rights only to TEST deploy.
The client requires root peer credentials. It sends one bounded JSON record
(`version=1,lane=test,active,inactive`, the last fields exact distinct immutable
Base names) plus one SCM_RIGHTS FD of the held existing deployment lock. The
scanner must identify the connecting actual TEST UID using SO_PEERCRED, read
exactly one record and FD, require exact lock inode, and prove exclusive
ownership of the forwarded open description and lock conflict on a separate
independently opened FD. It must also verify the current symlink, completed
qualification, absence of a pending activation, exact candidate identity,
complete service/process and mount inventory, and absence of startup paths
to the old tree. Acknowledge with only `assertion-base-test-retention-v1 OK`
plus newline and close; any other result retains the old tree.
The scanner proves and does not remove anything. Kill or unavailable scanner
means healthy release still succeeds but cleanup remains incomplete.
