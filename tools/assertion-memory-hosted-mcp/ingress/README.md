# Stage TEST ingress only

These scripts operate on private staged copies and never contact a server,
reload Nginx, call the Yandex CLI, publish a spec, change a credential,
or enable Sites. Use only on the verified host under separate authority.
No live endpoints or credential values belong in this repository.

- `python -B -m unittest test_stage_nginx_pair test_stage_test_gateway`
  in this directory. The gateway planner requires PyYAML.
- `python -B stage_nginx_pair.py --baseline <staged-live-copy> --approved-candidate <staged-approved-copy> --new-stage-dir <new-private-directory>`.
  Existing parents must be trusted. The tool writes only into a newly
  created mode-0700 private stage, and emits hashes and counts only.
- `python -B stage_test_gateway.py --baseline <staged-get-spec-copy> --expected-origin <verified-https-origin> --output <new-private-file>`.
  Read-only `yc serverless api-gateway get-spec` is the supported source
  for the baseline. Check its correct actual gateway instance independently.
  The spec planner enforces seven explicit baseline paths, no wildcard
  or any-method, the exact previous TEST path and independently verified
  HTTPS origin, and adds one exact POST plus two explicit forwarded headers.
  Semantic deep comparison proves the rest of the parsed document stays
  identical; YAML comments/whitespace are not byte-preserved. If aliases,
  drift or duplicate keys are present, stop for manual review.

## VM Nginx operator validation

Require the existing production transition unarmed: no transition pending
journal, no run-directory permit, no settled marker, no running fallback
timer/service. Obtain the existing exact guard fence lock exclusively;
keep it held through revalidation, any approved installation and rollback.
Prove no other exact or wildcard conflicting route in the full active
Nginx include graph without logging the config. Use safe no-symlink trusted
copies and pin original bytes and modes. Stage both proposed files; validate
each against the actual include graph using an isolated private mount
namespace that bind-mounts the proposed file at the actual config path
only within that namespace. Run nginx -t in each namespace. Stop if this
cannot be done; don't validate a guessed partial config or replace live
files just to test. Operators can recover the pre-change pair from
baseline.original and approved.original; the two eventual per-file renames
are not collectively atomic. No prod operation may arm between them.

Existing guard still expects exactly two approved substitutions. The same
additive TEST fragment is inserted into both buffers. Its future freeze or
rollback should leave this new TEST route untouched once intentionally
installed, while an operator rollback of this separate addition restores
both immediate original buffers. Check exact old hashes before any
separately approved installation. If Nginx check/reload/empty-body probe
fails, restore both original files under the same lock. No product data
queries are part of these tests. Once loaded, unauthenticated or
wrong-credential POST to the new TLS route should return 401 with no body
inspected; do not use an insecure TLS flag.

Keep the new service loopback-only at its previously qualified TEST port.
Explicitly preserve old gateway paths and all PROD config and data. The
planner passes no header besides the two declared for the new HTTP
origin. An authenticated TEST proof, publish and Sites token injection are
separate coordinated actions, not side effects of staging this candidate.
