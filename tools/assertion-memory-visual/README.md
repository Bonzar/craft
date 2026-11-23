# Assertion Memory Visual

The source fixture is the four pinned compiled assets from the existing private QA v2
Site; it is not the original editable frontend package. Packaging rewrites only the
HTML asset and Worker paths to be version-relative, matching the earlier approved seed.
No personal graph records, tokens, or live environment identity are part of the archive.
The UI and workflow contain no deployment configuration or data. It is the backend and Sites, not the UI binary, that enforce graph privacy.

`node tools/assertion-memory-visual/build-archive.mjs tools/assertion-memory-visual NEW_OUTPUT.tar` builds the same deployable QA archive
reproducibly. The original QA import produces archive SHA256
`0834ce7351ca874a23081dd0742b20f2a56f331454d2ef6f61f4073a7296d23d` and visual release
manifest SHA256 `82b9cf02fcd309ddda61e6f7af8ee5197ab3877e625066bbf846aec5532fc4e4`.
Later source edits require updating the manifest and create a different release.

After one-time provisioning:
1. Publish an immutable GitHub tag `assertion-memory-visual-vX.Y.Z` on a reviewed commit
   in the protected main of Bonzar/craft. The workflow packages once.
2. Run **test** with the release ID. It downloads the original artifact, verifies
   metadata/bytes, activates TEST through the visual-only receiver, verifies graph-independent
   asset smoke and emits immutable proof (release ID, exact source/artifact/hash, TEST run ID).
3. Run **prod** with that release ID and its successful TEST run ID. The resolver compares
   artifact identity and bytes to TEST proof before the workflow receives deployment/signing
   credentials. It does not build again. Changes require a new release. Retain build and TEST
   receipt artifacts for the intended promotion/rollback window, currently 90 days.

Before any live use, provision and verify both GitHub environments with main-only deployment
restrictions and no other main deploy workflows; gate production to the authorized caller/owner.
Enable immutable releases on this repository, require reviewed protected main, and pin production
provenance to the expected repository. Branch and execution controls must be verified against the actual account
before declaring the deployment authorized.

Use **separate** visual TEST and PROD accounts, isolated forced login/shell ignoring user startup
files, restricted SSH keys, root-owned receiver and `/etc/assertion-memory-visual/<scope>/allowed-signers`,
each with a separate deployment signing key `visual-<scope>`. Never reuse base keys, enable
forwarding or interactive login. The VM verifies the signed scope and archive SHA using its
independently root-pinned public signing key, then verifies archive/member hashes. GitHub environment
secrets are scoped SSH key, host pin, signing key, and corresponding visual gateway token. Never
write them to Git, artifacts, or logs. The signed archive is retained immutable. This candidate
needs live verification of archive retention/rollback, provisioned trust and network; smoke failure
after activation is surfaced, not automatically rolled back.

A one-time owner-private Sites shell deployment per environment preserves the existing graph relay and
owner gate. Only exact TLS ingress routes lead to the loopback read-only asset listener; the shell
passes a dedicated scope token to that route. Ongoing builds switch VM visual pointers, not Sites,
so no private Sites deployment credential is required in GitHub. Check exact original QA rendering
and authenticated graph integration on TEST before authorizing PROD setup.

Not provisioned: protected CI environment policy,
SSH/verification identities, restricted TEST asset receiver/origin and Gateway route, Sites TEST
shell and credential, owner graph acceptance, PROD counterpart, and actual GitHub TEST/PROD runs.

Bundled Three.js r187 is distributed under MIT; see THREE-LICENSE.txt.
