# Review-only Base production recovery candidate

The installed production service hook is host-managed and intentionally not
copied here. A live deployment rollback was blocked because the hook accepted
only the reviewed Codex drop-in, while systemd now reports both the authorized
Monium and Codex files. This patch does not install, restart, change privileges,
or deploy anything.

From this directory, run `python3 -B -m unittest test_transform -v`. For a
candidate on a review machine, use:

    python3 -B render_hook.py path/to/baseline.py independently-approved-sha256 new-candidate.py

The exact installed baseline digest must be verified independently first.
Review that the generated file changes only the existing drop-in validation
and the two new root-managed pin path constants. Unknown/extra paths,
wrong order, wrong digest and unsafe ownership/mode remain denied. Without a
Monium pin, the known Codex-only and no-pin/no-drop-in modes are preserved.
No symlinks, shell aliases or blanket allowlist. The existing graph policy is
unchanged.

For a later authorized host change, independently review the Monium drop-in
bytes and use a separate root-managed pin file; copy the established Codex
pin's actual owner/group/mode and verify the existing reader can read it,
rather than assuming a specific mode or adding access. Keep the existing
deployment inactive/idle lock and operate only via the normal release
gate. Verify old release health first through the actual read-only checks,
and preserve the pending activation journal: never remove it manually;
the protected receiver will clear it only after a successful restore/smoke.
