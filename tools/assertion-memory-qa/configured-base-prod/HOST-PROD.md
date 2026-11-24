# Configured Base PROD: source-only, not enabled

Install only after a reviewed configured TEST has proven the exact artifact.
The authorizer owns a **new** nonce DB; the existing PROD writer and graph DB
are neither copied nor reinitialized. Keep old source, key, service, and proof
lane intact while diagnosing. Do not touch settled marker or rollback witness.

- Registry dispatch for Base: existing TEST provider for TEST and
  `configured_base_prod_provider.activate` for PROD. Keep one already
  proven Base health function for both; the canonical receiver also executes
  its existing gated service-hook start/verify. Only the PROD environment
  sets `RELEASE_APPROVED_CONTROL_SHA`, `RELEASE_VM_ID`,
  `RELEASE_OIDC_SUBJECT`, `RELEASE_ENVIRONMENT_NODE_ID`; re-use other
  standard env variable names but with a **different** scoped SSH key.
  Environment SSH account name comes from the fixed Base profile.
- Install receiver-entry.py (0755), forced-command.py (0755),
  authorizer-entry.py (0644), and the gate module (0644) as root in
  /etc/assertion-release/configured-base-prod. Stage exact reviewed sources
  preserving code/tools/... layout under that directory: configured_base.py,
  experimental/configured_provider_checks.py and its offline imports from
  tools/assertion-memory-release. No checkout is user-writable. Preflight
  imports under /usr/bin/python3 -I -B as both service and writer UIDs.
  Existing verified root-owned legacy modules are loaded only from
  /etc/assertion-release/production/authorization and remain unmodified.
- Dedicated shell account assertion-cfg-base-prod-deploy with root-installed
  forced-command.py; independently verify sshd Match User policy, no ordinary
  shell/pty/port forwarding/env, root-owned authorized_keys with one reviewed
  new public key. ForceCommand and allowed SSH_ORIGINAL_COMMAND exactly
  configured-prod-receive. Validate sshd -t and effective settings.
- Exact sudoers rule (validate before install and with sudo -l):
  `assertion-cfg-base-prod-deploy ALL=(assertion-production-deploy) NOPASSWD:NOSETENV: /etc/assertion-release/configured-base-prod/receiver-entry.py ""`.
  Explicit empty argv matters. Legacy writer's current SSH shell and key are
  unchanged. Ensure its group matches the socket template's SocketGroup.
- Root-owned policy.json has exactly writer_uid and policy. policy contains
  repository_id, control_sha, vm_id, subject, environment_node_id, actor_ids
  (list). Match verified official issuer claims from exact same workflow shape;
  I/O against GitHub public OIDC issuer only. Approve one exact main SHA at a
  time. Start only dedicated socket after unit verification and permissions.
- Staging gates: foreign or repeated nonce, legacy issuer claims, changed
  control SHA/ref/artifact, implicit SSH shell and wrong inputs must fail.
  Keep writer payload bounded by current canonical receiver; check exact SHA
  before staging by its existing code. Don't make a valid deployment merely
  to check connectivity. A first real PROD deployment must be separately
  attributable to successful same-run TEST and protected PROD approval.
  Compare old writer ID/UID, root inode, DB inode, services, pointer and lock
  first/after without outputting records, tokens or key material.
