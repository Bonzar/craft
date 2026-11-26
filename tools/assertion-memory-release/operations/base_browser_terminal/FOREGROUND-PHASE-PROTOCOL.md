# Protected Base one-run phase qualification (offline candidate)

The installed configured writer and its settled helper are UID 986, not root.
Existing authorizer UID 980 is predeployment only. The deploy-owned journal cannot
authorize a root observation, and an ordinary permit cannot authorize v3.

For one exact approved run, the existing foreground root operator can pin the
previous and candidate signed artifact pair. The native receiver must pause
at two branch-specific checkpoints (after pointer switch, before restart or
restore). It must not hold the transition fence at these waits; the ordinary
existing deploy lock remains held. The root operator uses
`publish_one_observation` under the short existing transition fence. It validates
the already admitted pair from the authorizer-owned record, physically observes
exact current and manifest/journal consistency, and durably publishes only the
root-owned phase. The guard independently checks exact signed selected identity
before invoking its no-new-privilege fixed same-user restart. A candidate phase
may advance to restoring the exact predecessor; restoring cannot re-arm the
candidate. Neither phase grants a generic systemd action. Existing
post-restart proof and same-PID listener smoke remain mandatory, and the
transition fence is not held during HTTP smoke.

The source fixture exercises phase identity and rollback monotonicity. It does
not supply the real native waiting, root operator bootstrapping or release
custody, and is not usable by itself. If verification or the status of the
foreground process is ambiguous, do not switch the edge. The ordinary restored
predecessor remains on the prior lane only when the full native inverse and
terminal verification have succeeded; do not use marker deletion as a
substitute for the protected inverse.

Do not activate the product marker merely to test this work. It intentionally
makes v1/v2 insufficient. A one-run foreground process proves a migration,
not readiness for future automatic releases. The normal production workflow
must either bring its protected v3 applicant, pinned phase/terminal execution
under a preexisting supported root authority, or explicitly commission the
smallest such authority through immutable reviewed operations before
activation. No restart of an unreviewed v1/v2 lane can provide that proof and
there is no automatic V2 fallback. No new keys, roles, owner sessions, database
writes, network listener, or general command broker are provided here.
