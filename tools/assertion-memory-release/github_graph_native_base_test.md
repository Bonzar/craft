# Native base TEST deployment

An original immutable base build completion can enter a real TEST deploy in
the same native release graph. The deploy uses the existing resolver, archive
unpacker, restricted TEST sender/receiver and concurrency group. The native
evidence is deliberately not a legacy PROD receipt. Visual and PROD deployment
are unchanged; a manual graph preview does not deploy.

Before enabling, independently verify protected main, workflow change controls,
and the dedicated assertion-memory-native-base-test Environment (main only,
required reviewer, no administrative bypass). Place TEST-only sender variables
and pinned transport credentials only in that isolated Environment; do not set
these at repository scope. Adopt/verify that exact TEST principal separately.
Set environment-scoped ASSERTION_MEMORY_NATIVE_BASE_TEST_ENV_READY=true only
after checking those controls, then opt in with the repository variable
ASSERTION_MEMORY_NATIVE_BASE_TEST=true. Leave both flags unset while in review.
A readiness variable is not proof of reviewer settings; verify them separately.
Never set the opt-in before creating the dedicated Environment: GitHub can
otherwise create an absent Environment without approval rules.
