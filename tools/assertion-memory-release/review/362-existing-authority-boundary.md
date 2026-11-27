# Deployment authority boundary (source-only)

Read current main on November 27 without host changes:

- Base TEST receiver `tools/assertion-memory-qa/vm/receive.py` blob
  `327a520df23290d56c6bb1449e3591bdc8753675` authenticates the existing restricted
  SSH account `assertion-test-deploy` and only binds stage/revision/digest in
  command. It does not participate in the protected PROD authorizer path.
- `tools/assertion-memory-qa/prod-bridge/authorizer.py` blob
  `64e46118acf081d8e8e2448840bab07f4a408d35` admits exactly one configured
  peer UID using SO_PEERCRED. Its accepted config keys are only deploy_uid and
  policy. The signing policy in `reusable_proof.py` binds the production
  environment, not a TEST receipt issuer. Current receiver gate blob
  `ecb4f86cba360ad0fb702c899d29e25388d468e5` reaches only
  /run/assertion-release-authorizer/production.sock and drops the typed Intent
  before calling the actuator, forwarding only revision/digest/stream.
- Visual packed receiver `runtime_host.py` blob
  `eafc716aac16a485bceb5e24aa49045470d525af` uses its stage-local configured
  signers and packed framing, not the Base Unix authorizer. Its runtime
  receiver type carries stage, archive and pair identity, not a trusted
  cross-lane family journal or an independently reviewed fix decision.

No existing capability authorizes two such peers to own a single durable
same-family lock. Reusing the PROD socket would broaden its fixed peer/stage
policy; world/shared-group access or feature flag is not an equivalent gate.
The separate unmerged #364 is not a currently provisioned authority and is
not a dependency. Coordination remains dark pending an independently
approved existing privileged operation that can verify both lane identities
and own one atomic per-family ledger without widening deploy UID privileges.

Local test_forward_release_process_race verifies the policy/Checkpoint
implementation using Linux fcntl locks and a shared SQLite file across two
processes under the same trusted fixture user. The Base candidate's pending
native TEST switch excludes stale Base PROD, but does not block Visual;
once TEST succeeds the stale old Base can never promote. Exact TEST replay
only reads an actual witness; exact new Base alone promotes. This test does
not assert that actual deploy accounts can share the fixture authority.

Minimal typed native protocol: reserve an exact TEST or PROD attempt using
independently verified protected workflow/run, immutable artifact and prepared
identity; for in-train replacement also verify the exact reviewed defect
evidence. Record same-family candidate supersession by atomic compare-and-swap
before effects. Commit only from exact actual service artifact and generation;
after a lost reply, reconcile that same attempt against native state without a
second switch. TEST and PROD must share this per-family authority, and a runner
cancellation cannot clear its pending attempt. No credential, publisher,
listener, permissions, or repository settings are proposed here.
