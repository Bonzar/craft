# TEST release wheelhouse

The TEST receiver installs from a separately staged, root-owned wheelhouse.
Use wheels for the deployed Linux and `/usr/bin/python3` version. Copy reviewed
public wheel bytes, not the PROD directory or its permissions, credentials, or
scripts. Check each wheel against the reviewed SHA-256 lock before provisioning.

Under `/etc/assertion-release/test`, provision:

- `wheels/`: root-owned, mode 0755, with regular root-owned `.whl` files,
  non-writable by group/others, no symlinks or hardlinks.
- `locked-requirements.txt`: root-owned, mode 0644, no symlink or hardlink.
  Each line is `name==version --hash=sha256:<64 lowercase hex>`; multiple
  hashes on the same line are allowed. No options, URLs or nested files.
- `approved-requirements-mcp.txt` and `approved-requirements-graph-lexical.txt`:
  root-owned, mode 0644, the byte-exact corresponding dependency manifests
  from the verified Base artifact (not fetched at deploy time).

Review wheel and lock updates together against both manifests before resuming
deployment. Validate the offline installation in a disposable environment as
the TEST service identity; no fallback to an index. Install the hook only after
those checks. Readiness and activation continue through the normal release
workflow; an existing live venv is never cleared by the prepare action.

To check the hook and lock parser without a live deploy:
`python3 -B tools/assertion-memory-qa/vm/test_offline_wheel_prepare.py`.
