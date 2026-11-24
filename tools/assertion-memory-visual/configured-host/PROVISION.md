# Configured Visual TEST / PROD receiver (source candidate)

One receiver and launcher, two exact SSH identities. No command below runs on
a live host automatically. The renderer produces inert per-stage files and
fails if the requested output directory exists.

1. Before either stage: confirm canonical legacy reader/writer and their lock,
   serving pointer, UID/group/state permissions. Required existing roots:
   /etc/assertion-memory-visual/{test,prod}, /etc/assertion-release/shared,
   /var/lib/assertion-memory-visual/{test,prod}. Never chmod/reown, add ACLs,
   or replace files in these legacy roots.
2. Generate only TEST's inert configuration:
   python3 -I -B render.py test /root/review-configured-visual-test
   Confirm TEST receiver and launcher hashes, install both root-owned under
   /etc/assertion-memory-visual/configured/ (receiver mode 0644, launcher 0755,
   directories 0755). Install only root-owned configured/test/allowed-signers
   and root-owned /etc/ssh/assertion-visual/configured-test.authorized_keys.
   Expected signer principal is visual-test. SSH transport key gets restrict.
   The four signing and transport public keys across stages must be distinct
   from each other and from all legacy keys. Leave PROD absent.
3. Create locked configured account assertion-cfg-vis-test-deploy with own
   unused group, no extra memberships, root-owned empty home at
   /var/empty/assertion-cfg-vis-test-deploy, exact installed launcher as shell.
   Receiver runs as existing assertion-visual-test-deploy, never root, via a
   single exact command. Scope SSH and sudo changes to configured account.
4. Validate both rendered fragments via the host's sshd -t, sshd -T -C for new
   identity plus legacy/admin, visudo -cf, and sudo -l -U preflight. Confirm
   binary passing (no PTY, no input/output logs); for new identity prove wrong
   mode, shell, old signer/key and wrong argv reject before state changes.
   Confirm TEST-only forced command and unchanged canonical TEST pointer.
   Reload only after verifying rollback route retaining admin/legacy access.
5. Perform agreed GitHub TEST and verify pointer, smoke and same-run receipt.
   Only after exact TEST evidence, independent PROD review and authorization,
   repeat steps 1–4 with stage prod, separate keys/approval/GitHub environment,
   configured/prod/allowed-signers principal visual-prod, configured PROD
   SSH account and root-owned empty home. Before PROD live, confirm shared
   receiver and launcher hashes identical to the approved TEST source; do not
   overwrite shared installed code while TEST usage is active. A new version
   needs its own preflight, freeze, and coordinated atomic installation.
6. Rollback per stage: disable only its GitHub gate, remove only its transport
   key/config fragment, recheck exact legacy GitHub and SSH operation. Leave
   canonical state and contents unchanged.

The role separation and exact argv are source-tested. An executed host
integration and actual binary stream tests still require root authorization.
