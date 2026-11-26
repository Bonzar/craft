# Existing-receiver provenance contract (deployment only)

* Base PROD's existing protected challenge/confirm already verifies an Intent
  binding production run ID, tested run, TEST receipt ID, immutable Release ID,
  build run, artifact ID, SHA and archive digest. receiver_gate.handle currently
  discards that typed Intent from its activation callback. Retain it after the
  trusted root socket confirms existing authority, construct ProtectedDispatch
  from it plus independently fetched provider identity, and compare live TEST
  generation to the verified TEST receipt. No new signer, key or env privilege.
* Base TEST's existing SSH command binds stage/revision/digest but lacks run/ID.
  For forward versions add a distinct receiver capability with bounded framed
  metadata bound to the existing protected controller. Verify Release ID,
  protected TEST workflow run and artifact independently; never treat framed
  fields (or environment) as authority. Keep legacy exact in-flight paths.
* Visual's signed packed frame binds static/runtime bytes and mode; connect
  existing protected workflow provenance to the packed activation callback at
  runtime_host.receive -> runtime_joint.activate_verified. If the current frame
  does not expose verified Release/run IDs, upgrade the signer and protected
  receiver together. Do not authorize from static receipt supplied by caller.
* The review witness must prove the defect decision and recorded failing TEST
  on exact old artifact, exact replacement and scope hash. Accept no caller
  booleans. Read real service witness for both products; static pointer or HTTP
  status alone does not show actual deployed revision, digest and generation.
* Use fixed privileged configuration for release workflow ids, durable shared
  family locks and ledger. Both lanes must coordinate the family lock; reject
  missing capabilities rather than create per-lane fallback or grant deploy.
  Current CHECK/patch is dark, no protected installer changed.
