# .35 witnessed build material

Use original successful build job 139935158583 in immutable release workflow
run 45827455430 to identify the exact filenames/versions. The 30 release
wheels have independent upstream PyPI hashes; graph lock has 31 matching
npm Linux x64 glibc tarballs. Preserve Python 3.12.16, Node 24.22.3 and npm
11.19.1. Do not substitute local Python 3.12.14 or a floating dependency.
FastEmbed excluded exactly like the normal release lexical profile.

On an approved isolated build-only runner with registry egress allowed only
during package acquisition, use protected tools and exact files:

    python3 materialize_035_inputs.py /absolute/new-offline-input-035
    cp base-035-python-full.lock /absolute/new-offline-input-035/python-full.lock

This script reads only canonical public package metadata/tarball endpoints,
rejects redirects and verifies bytes against reviewed pins before installation.
No direct credential or deployment rights required. Do not run acquisition on
the VM or from a historical checkout. The actual compile uses the checked
image and package folders in a fresh network namespace/container; no npm
lifecycle scripts. For the proof, compile twice from exact Git tag in fresh
isolated cold roots and compare qualified package SHA256 and manifests before
the independent sealer. Do not reuse the archived .35 bundle as a build input.

Here the exact Node/npm binaries are installed; Python patch differs and no
npm/npm tarballs or wheel material is available. The first genuine network-
off npm run failed on uncached Vite 8.3.8, and uv had no MCP 1.30.0 wheel.
No registry downloads were attempted in this workspace. Existing approved
Base job does not expose a historical build-only dispatch: release runs only
build on immutable publication, dispatch branches are TEST and PROD. Use
reviewed no-secret build-only #333 specimen, never dispatch a deploy substitute.

The exact verified setup-python action from original job is
`actions/setup-python` with the original full
pin `a26af69be951a213d495a4c3e4e4022e16d87065b`; supply exact
`python-version: '3.12.16'`, never `3.12`. Original GitHub hosted runner
was Ubuntu 26.04 image version 20261116.88.1. The final mounted isolated
toolchain root should be read from protected allowlist and independently
verify the exact setup action/downloaded CPython build digest, Node official
download digest and installed npm version; patch version equality alone is
not physical image provenance.

With a reviewed immutable toolchain root that has these binaries self-
contained beneath its `bin/`, and a clean exact `.35` checkout:

    bash compile_035_offline.sh /absolute/reviewed-035-checkout \
      /absolute/new-offline-input-035 /absolute/new-output-035 \
      3.12.16 /absolute/proven-toolchain-root

This combines a new network namespace with a minimal filesystem sandbox.
Dependency acquisition and compile phases are separate; compiled results
write content hashes as an unsigned reproducibility witness, not an OIDC
signature or receipt suitable for deployment. Compare outputs from two fresh
roots. There is no deploy capability in either phase.
