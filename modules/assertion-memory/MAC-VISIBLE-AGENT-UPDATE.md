# Enrolled Mac capture: public agent turns (#426)

Use only the protected, reviewed owner updater and a complete authenticated local
Git checkout containing `e72c87801b2a4ec1828886085972cf3ff273fe45`. This package
accepts the installed #411 reader or the exact #426 replacement and changes
only `lib/native_capture.py`. All runtime companions, hooks, PROD capture
profile, endpoint/cutover and SQLite outbox are preserved. No Base or Visual
release is needed: the transport implementation is byte-identical.

Read-only, with the capture worker running:

    python3 tools/assertion-memory-mac-visible-agent plan --checkout "$PWD"

For an explicitly authorized install, ensure no capture hooks are running, stop
only the verified exact capture worker gracefully, then use:

    python3 tools/assertion-memory-mac-visible-agent apply --checkout "$PWD" \
      --operator-quiesced --confirm-existing-prod-binding

A held worker lock or any mismatched file/profile pin fails without a runtime
change. In the same maintenance operation restart the identical collector with
its existing binding and state, even if the apply fails, and verify new loaded
runtime provenance without sending a test to PROD. Closed-session history must
not be replayed. `plan` should then report `preflight_ok_already_current`.

Rollback requires independently confirming no database clear, quiescing the
same collector, and invoking `rollback` with `--confirm-no-clear` and
`--operator-quiesced`. The protected rollback directory contains only the
previous code bytes and the binding digest, never credentials or queue data.

## Reproduce the isolated acceptance

Supply read-only local checkouts with predecessor
`ba4e0bf2b06063b02fd260f77cd3397db629efd4` and target
`e72c87801b2a4ec1828886085972cf3ff273fe45`, respectively:

    ASSERTION_MAC_UPDATER_OLD_QA_CHECKOUT="$OLD_CHECKOUT" \
    ASSERTION_MAC_UPDATER_NEW_QA_CHECKOUT="$TARGET_CHECKOUT" \
      python3 -B -m unittest \
      tests.test_assertion_memory_mac_visible_agent_updater.VisibleMacUpdaterTest

It creates its own fictional profile and SQLite outbox, exercises the exact
old worker's capture and acknowledgement, closes and reopens the outbox with
the new reader, and verifies the same cursor, no old resend and one new batch.
The receiver is local and synthetic; no external network, account or installed
settings are used.
