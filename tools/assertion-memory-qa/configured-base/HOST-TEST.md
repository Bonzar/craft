# Configured Base TEST transport (review candidate; not activated)

- Selected Actions stage must use exact pending generic TEST GitHub Environment
  and authenticate its approval using the common verifier before calling
  `configured_base_test_provider.activate(ctrl, release, content, payload_dir,
  promotion, env)`. No PROD invocation allowed.
- Only configured TEST Environment receives dedicated SSH host pin and key
  (`CONFIGURED_BASE_SSH_HOST_PIN`, `CONFIGURED_BASE_SSH_KEY`).
  Fixed public inputs: CONFIGURED_BASE_HOST, CONFIGURED_BASE_PORT,
  CONFIGURED_BASE_VM_ID, CONFIGURED_BASE_ENV_NODE_ID,
  CONFIGURED_BASE_REUSABLE_SHA, CONFIGURED_BASE_TEST_READY and RUNNER_TEMP.
  Reusable actual job SHA must be checked against root-owned policy/pin.
- SSH user `assertion-cfg-base-test-deploy` is not a TEST owner/member and must
  never have a standard shell, generic sudo or writable route/config. Root
  creates one dedicated key entry and exact forced restricted command:
  `ForceCommand /etc/assertion-release/configured-base-test/forced-command.py`.
  Use that same root-owned restricted script as login shell, wrapper verifies
  the exact sshd `-c` arguments and fixed SSH_ORIGINAL_COMMAND.
- Candidate sudoers delegation (independently validate exact matching with
  visudo and effective account; no wildcards/legacy credentials):
  `assertion-cfg-base-test-deploy ALL=(assertion-test-deploy) NOPASSWD: /usr/bin/python3 -I -B /etc/assertion-release/configured-base-test/receiver-entry.py`.
- Install only reviewed, exact owner-root versioned files under
  `/etc/assertion-release/configured-base-test`, no legacy TEST paths changed.
  Place entry/gate and exact reviewed dependency copies (reusable_proof.py
  and authorizer.py; public fixed-issuer helpers, no settings copied).
  Root-owned policy JSON with exact `reusable_sha`, `environment_node_id`,
  `vm_id` and `repository_id`; package pins remain independent from legacy.
  New nonce state directory `/var/lib/assertion-configured-base-test` owner
  canonical TEST writer, mode 0700. Verify system Python cryptography import.
- AUTH peer may only request a bounded challenge or stream a payload with the
  exact signed audience, GitHub Environment/Ref/Reusable SHA/run/event/nonce.
  Failed proof cannot invoke the canonical `base_payload.deploy` lock or
  touch TEST data. A valid signed proof is consumed once before the original
  same-owner TEST receiver algorithm stages, activates, smokes, or rolls back.
- Before enablement: independent exact runtime review, pin common workflow
  and actual repo API proof shape, verify disarmed GitHub environment and VM
  bootstrap, prove admin/legacy scope unchanged, deny id/PTY/forwarding,
  verify no-archive challenge and signed-token checks using the non-deploying
  fixtures (there is no live no-archive confirm operation), then one approved
  scoped TEST deployment on the user's existing immutable release. Never
  accept PROD or fall back to legacy authority.
