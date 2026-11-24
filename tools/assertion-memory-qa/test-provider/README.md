# TEST MCP environment-proof provider (unpublished)

Consumer: the owner-private TEST Sites MCP Worker in
`mcp-test-site-candidate/exact-source/test_vm_site.mjs`. This VM adapter has
one loopback endpoint, not a generic MCP transport or general SQLite API.
No secrets, release metadata, VM changes, DNS routing or Sites deployment
are included. Its 200 reply contains only the TEST tag, fixed fixture ID and
boolean synthetic marker. No fixture content is returned.

## Preconditions, not currently verified live

1. Complete release-managed base TEST first. Confirm a GitHub-approved TEST
   immutable artifact: its exact tag, 40 lowercase commit SHA, and 64
   lowercase served archive SHA. Cross-check all three in the release
   receipt. Confirm `/srv/assertion-release/test/current` matches that exact
   TEST release. Do not map a fabricated tag onto the legacy synthetic DB.
2. Require actual currently running `assertion-test-deploy-graph.service`
   and `assertion-test-deploy-mcp.service`, at 127.0.0.1:8891 and :8893. The
   provider validates each exact unified TEST cgroup and its single member,
   release cwd, exact argv, listener
   process/loopback port, TEST MCP config DB and fixed TEST SQLite inode on
   both sides of a consistent read. Use the same isolated TEST SQLite as
   Graph+MCP. If a legitimate release changes their argv, validate the
   trusted release and explicitly update this source, not the permission
   boundary. Do not weaken /proc restrictions if checks fail.
3. The reviewed scope is not the legacy 8791/8793 compartment and does not
   automatically switch to it. It is not PROD and has no PROD parameters.
   Neither inactive scoped services nor a stale current link is readiness.
4. Run only as existing unprivileged TEST service UID (not root or PROD),
   Python 3. Bind fixed 127.0.0.1:8831, no separate public socket. Prefer a
   root-managed systemd unit with fixed User, ExecStart and credential sources,
   NoNewPrivileges, empty capability bounding set, ProtectSystem=strict,
   ProtectHome=yes, PrivateTmp, and read-only TEST data access sufficient to
   read the existing WAL/SHM. Preserve read-only access to the same-user proc
   and exact TEST cgroups needed for attestation. The provider does not use
   the user systemd manager bus. If kernel policy
   blocks this under the actual unit, fail closed and design a fixed
   privileged-side attester; do not run the request handler as root, grant
   ptrace capabilities, or change permissions at runtime.
5. Issue a new TEST-only 32-byte random credential (64 hex chars). The
   private TEST Worker stores the bearer server-side; only the SHA-256
   lowercase hex digest of its literal ASCII value is loaded into the VM
   service as systemd credential `test_bearer_sha256`. Independently load
   root-managed `test_release_identity` JSON with exactly
   `{"tag":"…","commit":"…","archive_sha256":"…"}`, not supplied by requests.
   Disallow direct read of TEST MCP token/graph credentials from adapter's
   service where practical; it does not use them. Never reuse PROD, legacy
   QA, or Visual credentials. Do not print/store the bearer in URLs, logs,
   shell history or source.
6. If authorized separately, add only an exact HTTPS gateway POST route
   `/memory-mcp-test` to 127.0.0.1:8831 through approved routing. Preserve
   one unmodified `x-memory-mcp-test-auth` header; reject multiple values,
   query strings and requests over 512 bytes; no caching, request tracing,
   redirect or unrelated route changes. Confirm real DNS/TLS from Sites and
   no plaintext intermediary to the VM. A local service test cannot prove
   gateway registration, actual Sites identity forwarding or that the
   separate visual is reading this same TEST release.
7. Before enabling TEST Site flag, verify no/wrong credential denial, exact
   authenticated 200 response, wrong fixture, route/operation and stale or
   unavailable services fail closed, then prove real account-installed
   plugin and TEST visual read the matching DB. Keep PROD unchanged.

Local `python3 -B -m unittest -v` exercises the actual HTTP contract, canonical
fixture DB creation in a temp directory, no creation on missing DB, rejected
mutated scope, accepted-response shape, no caching, denial of mis-scoped
requests, listener ownership, and generation replacement race. No live
VM/GitHub/Sites authentication or deployment was performed.

### WAL and process isolation acceptance

Run the unit with a kernel read-only mount of the entire TEST state directory,
not merely `mode=ro` on SQLite. Verify `findmnt` from the provider process's
mount namespace and ensure there is no writable override nested under it, no
bind of the TEST state directory with writes, and no mount capability. The
provider independently refuses the path unless the mounted state directory,
main DB, and extant sidecars all report `ST_RDONLY` (and are not symlink aliases).
On a WAL DB whose sidecars are missing and cannot safely be opened, return 503
rather than creating them, changing SQLite journal mode, or using
`immutable=1` against a busy live database.

A normal consistent SQLite read can take shared / WAL read locks. The contract
is *no DB, WAL or SHM content writes/creation by this process*, not lock-free
reading. The read transaction is short lived with a 300 ms busy timeout.
Local Linux same-UID subprocess isolation allowed reading sibling `cwd`, `fd`
and `net/tcp` without adding privileges. An untrusted environment variable
or request cannot alter the cgroup path. Empty, multi-PID, unexpected, or
changing membership is rejected rather than guessed. Live VM policies may differ; the
root-owned service should still have no capabilities. If the VM returns
EACCES, preserve the kernel policy and report 503 while a fixed trusted
attestation alternative is reviewed.


### Review and stage

- Run `bash -n install.sh`, verify the installer digest obtained with the
  reviewed branch, and run `./install.sh --check` locally first. It checks the
  provider and unit hashes and parses the Python source; it neither checks
  the server nor installs. Unit compatibility is checked separately.
- In an already-authorized VM admin session only, place the exact three
  files in a root-owned private directory as mode 0600 and repeat
  `bash ./install.sh --check`. A separately authorized
  `bash ./install.sh --stage` also verifies the unit using host systemd,
  checks that 8831 is unoccupied, then stages exactly the new root-owned
  provider source and service unit. Existing targets cause a safe refusal.
  No daemon-reload, startup, authorization key provisioning, routing or
  existing release activation happens in this script.
- Keep identity and bearer digest files root-owned mode 0400 and outside
  Git. Set them independently before separate, reviewed activation.
- Do not provision a new DB or promote an untested release just for this
  endpoint. The base GitHub workflow's successful TEST receiver does not
  intentionally shut down its services. If they are no longer live,
  inspect its TEST receipt and service state first. There is no standalone
  hold/start action in the reviewed workflow. Only a newly authorized
  exact-artifact TEST replay via the existing workflow or a separately
  reviewed TEST-only start mechanism may restore it.
