# Configured Visual TEST provisioning (review only)

New admission path to the existing TEST writer. No group-write/ACL on 2750 state,
ordinary shell, PROD changes, reuse of legacy keys, or manual TAR copying.

1. Record canonical TEST pointer/digest and effective SSH policy, never secrets.
   Verify Python/sudo and current exact writer/reader/shared lock on the VM.
2. Create root-owned configured-test config and empty home. Install root-owned
   receiver and launcher; confirm trusted directories have no symlinks.
   Add dedicated locked SSH account with own group and no extra memberships.
3. Generate distinct transport/signing keys in approved secret store; compare
   fingerprints against both legacy stages and configured PROD. Install signer
   public key only in configured-test/allowed-signers (principal visual-test);
   transport public key only in configured-test.authorized_keys, with restrict.
4. Validate SSH Match via sshd -t / sshd -T -C for new account, admin and both
   legacy lanes. Validate sudoers via visudo -cf and effective sudo -l -U.
   Command can run only the fixed isolated Python argv as old TEST writer, not
   root, with no shell or args. Disable PTY and input/output recording only for
   that command so binary data stays exact and is not retained in sudo logs.
   Reload only after a second reviewer verifies policy and rollback.
5. Prove cross-lane SSH and signer keys reject, shell request rejects, PROD
   command rejects before data access, and valid signature plus malformed
   archive leaves canonical TEST pointer unchanged. Prove fixed caller UID,
   target UID, isolated interpreter and explicit trusted paths.
6. Precreate configured-release-visual-test main-only with exact reviewer and
   no bypass; bind its verified ID and TEST-only CONFIGURED_VISUAL credentials.
   Keep stage/global enables false until exact GitHub caller/protected path,
   job label, environment-only secret mapping and approvals readback verified.
7. Run TEST only after authorization; validate canonical pointer/smoke and
   exact receipt for original archive. Leave PROD disabled; do not approve
   until it is provisioned and independently reviewed.
8. Roll back by disabling gate, removing only new key, new SSH Match and
   scoped sudoers; verify legacy config, running ingress and pointer unchanged.

Local fixture verifies scope, not actual SSH/sudo integration. visudo is not
installed in the candidate sandbox. Exact effective sudo policy and binary
stream checks on VM are launch blockers. No operation here has been run live.
