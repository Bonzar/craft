# Owner-managed machine enrollment v1 (Base integration draft)

This PR ships only a capability-gated browser client and a wire contract.
No historical environment advertises `owner_machine_devices_v1`, and this
does not create a credential, grant permission or modify the existing registry.

The device generates a random, existing-format 64-lowercase-hex key in its
own secure store; only the prefixed `sha256:<SHA-256 ASCII key>` is copied
to the owner screen. The prefix is mandatory to reject a raw bare key pasted
by mistake. Device authenticates afterwards with the existing machine API and
its original retained key. No key goes in the URL, owner UI, request body,
clipboard automation, event stream, artifact, response or access log. Do not
enable request body/header logging on the protected owner API. The fingerprint
is an identifier, not a bearer; all device management remains owner-only.

- Advertise `capabilities.owner_machine_devices_v1=true` only for an actual
  owner-authenticated, configured and writable stage. Existing owner
  same-origin cookie; Origin/CSRF on writes; no alternate reader or Sites.
- `GET /api/v1/processing/devices`, no query; return
  `{schema:"owner-machine-devices/v1",stage,revision,items:[{fingerprint,label,revocable}]}`.
  Stage is `test` or `prod`, revision 32 lower-hex, fingerprints 64 lower-hex
  unique, maximum total 16. Existing config-owned fingerprints, gateway
  identities and designated auto-processing Mac policy are not browser
  mutable; config-owned machine entries may be listed `revocable:false`.
- `POST /api/v1/processing/devices/prepare` with normal owner session CSRF and
  exact body `{stage,expected_revision,action,fingerprint,label?}`. `action`
  is `enroll` (label required, trimmed 1–80 UTF-16 code units, no C0/DEL)
  or `revoke` (label absent; managed fingerprint only). Response
  `{stage,revision,action,fingerprint,operation_proof}`; no change yet.
  Use existing bounded operation proof format `<decimal issued ms>.<64 lower-hex>`;
  issue/record keyed once by stage, owner session, current exact registry
  generation, target fingerprint, label and action. Never reuse clear/queue
  proof authority.
- `PUT /api/v1/processing/devices` with identical body and operation proof in
  `X-CSRF-Token`. Require owner session plus same Origin and unused unexpired
  exact proof. Verify/revoke and consume proof in one transaction with the
  current generation; rotate revision. Return exactly
  `{stage,revision,action,fingerprint,committed:true}`. A timeout does not
  authorize retry; read current state first. Reject stale/foreign stage
  or unavailable store, duplicate key, all 16 occupied, invalid proof,
  configured immutable fingerprint, and race with another operation. No
  treatment of 202 as successful, no retries of writes.

Use the existing private `machine_authorized(header,digests)` verifier and
the same machine permissions. A stage-local writer must atomically merge
configured immutable machine fingerprints with owner-managed fingerprints
and resolve the current bounded set for **each** authenticated machine
request, including the effects lock; revoke must reject next request
without restart. Keep the existing designated Mac requirement fixed and do
not automatically broaden the fresh-Mac processing gate. Existing static
registry remains usable unchanged while the feature is dark. Persist only
fingerprints/labels/revision and owner-proof lifecycle in a private
stage-local store; never add records to capture/thought DB. One code path
serves both stages; no dynamic enrollment through Gateway/MCP/legacy read
authority. Opening the UI reads only; cancel, close, logout and navigation
discard local proof/fields.
