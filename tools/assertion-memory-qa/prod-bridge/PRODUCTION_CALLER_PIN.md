# Production caller pin update before wiring

This is source and tests only. It does not call the reusable workflow or change
existing release BUILD, TEST or PROD jobs. Update the byte-identity policy for
the native TEST policy imported by shared base TEST helpers, and require the
future caller to declare both protected main and an unset-by-default dedicated
bridge opt-in. The existing pinned OIDC/host authorization is unchanged.

After this source is reviewed, pin the audited resulting main SHA in the separate
caller PR. The pin must already contain the exact current BUILD/TEST prelude and
every listed helper, including any approved namespace fix. The separate caller
changes only the old disabled PROD job and its tests. Do not set readiness
before independent Environment, narrow reviewer/actor, signed claim, VM host
key, scoped principal, rollback and fail-closed receiver qualification.

Re-run TEST on the final caller main SHA using the existing immutable release
asset, not a rebuilt asset. The previously green TEST remains operational
evidence but is not the promotion receipt for a changed control commit.
