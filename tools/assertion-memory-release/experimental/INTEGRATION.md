The first deploy-neutral migration increment is a real protected-main
artifact resolver for both configured profiles, not another synthetic
builder. Put alongside the existing canonical verifier on adoption. It
resolves the published immutable version by tag and authenticates the
original source, build run, artifact and digest using unchanged routines.
The first displayed job should be "Build · verify original" for existing
versions; it must not claim it rebuilt. A distinct future first-build
transition is required for combining first build and deployment in one
run. It needs a protected entrypoint before checkout of the app/tag.

Do not copy the current reusable stages wholesale or remove their guards.
Visual currently requires the exact old top-level workflow_ref; its verifier
also relies on canonical TEST job identity and same-run origin. Base legacy
PROD requires explicit legacy workflow path, event, attempt and TEST proof;
the native Base TEST proof is expressly not accepted by legacy PROD.
The next receiver policy revision must add an exact generic caller/reusable
identity and verify canonical profile/environment/receipt. Update only
additive entries and preserve existing legacy identities. Bind at the
Python API of current stage validators, not by spawning tag-controlled
commands. Thereafter use a shared TEST and PROD job with separately
preflighted actual Environments and read-only artifact provenance, and
dispatch existing provider handlers behind a fixed profile registry.

A safe first live migration requires independent verifier/receiver review
and credential-free attestation of the new caller/ref/approval claims.
Until then this increment cannot serve as the completion of full deployment.
