# Assertion Memory visual release

This builds the four pinned native QA v2 rendering assets. Verify the MIT
notice included with Three.js. The tree contains no Site project state,
owner-specific credentials, graph snapshots, database or personal content.
The compiled QA assets are not recovered editable sources.

The visual builder retains the exact approved raw TAR and canonical JSON
manifest. Shared assembly validation checks those bytes without repacking,
then shared strict-host SSH carries a visual-scope signed envelope and
unchanged payload. TEST and PROD have separate transport keys, signature
policy and scope. The workflow verifies immutable source/build provenance
and PROD must reference the exact successful visual TEST receipt.

The VM uses one shared release implementation for staging, digest/manifest
qualification, atomic `current` symlink, synchronous origin smoke and
rollback. The visual adapter allows only the four declared assets and
manifest; all retained versions are under `releases/<manifest-sha256>`.
Clients already pinned to prior releases keep their versioned URLs. This
does not make Github's workflow artifact retention unlimited or claim
automatic rollback for a separate ingress or Sites failure after the
receiver has returned.

Install shared code once at `/etc/assertion-release/shared`; configure
independent root-owned visual scope entrypoints, fixed restricted shell and
signing policy. Do not deploy the previous detached pointer-file receiver.
Do not enable either scoped visual deployment until receiver negatives,
manifest/pointer, gateway and private Sites tests pass on the real host.
The synthetic tests do not qualify the actual attached services.
