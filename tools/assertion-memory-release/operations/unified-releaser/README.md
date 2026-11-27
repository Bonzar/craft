# Unified releaser release, source review only
One immutable tag/assertion-releaser-vX.Y.Z, one deterministic asset
assertion-releaser.tar.gz, one SHA-256 for both lanes, one parameterized
workflow and entrypoint with test|prod and apply|inverse. The branch,
workflow, environment, and current receipt are separate authorities.
Independent lane state in the existing canonical TEST/PROD directories
uses isolated locks, separately authorized operation receipts and fixed
operator bindings. Names in the specimen are proposed not installed.
Never derive directory, shell command or credential name from raw input.
A GitHub Environment gate is necessary but does not grant host access.
The current Base application archive cannot self-update its own installer.

GitHub can protect source review and package/verify the one immutable
asset against its exact trusted tag commit and expected digest. The checked
in workflow specimen is inert and fails closed for both lanes; it does not
request secrets, deploy, or enable a new policy. Review transport integration
takes a verified lane receipt (issuer/signature/expiry, unique operation id,
tag, objective digest, before/after/inverse/adapter, exact predecessor for
inverse) from the respective separately authorized control-plane actor.
No same-release replay: lock and durable one-use receipt reservation are
protected lane-local state before the first effect. Partial application
stays pending, not retried or inferred from a GitHub success string.

TEST bootstrap is independent: existing restricted TEST app deployment
receiver accepts only deploy-release test and cannot write the installed
hook. Prior TEST installer kit is evidence for exact b44bc450 baseline
renderer and two-unit order; it is not host authorization. Existing
separately authorized TEST control plane must qualify the installed
actuator and derive actual new digest in place from the immutable old
hook. No raw host bytes, credentials, Nginx, DB, PROD, or role changes.
The TEST action can first be qualified under existing review after receipt.

PROD bootstrap is separate: the existing PROD application promotion
authorization/receiver is not a releaser-installer authorization. Its
independent accountable operator must attest exact production installed
hook/adapter ownership, approved operation receipt issuer, old digest,
lock, recovery and own actuator. TEST approvals, signer, receipt, transport
and baseline cannot be copied. No PROD checks or activation in this change.

Contract tests use fake independent trusted actor/observer only, including
test/prod same artifact, cross-lane receipt denial, package mismatch,
forward/inverse exact pin, replay and incomplete settlement. Domain
installer procedures remain lane-specific; no generic shell passthrough.
Run: python -m unittest -v test_releaser
