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
