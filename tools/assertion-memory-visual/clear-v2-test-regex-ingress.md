# TEST edge when the owner BFF has a generic processing regex

This source-only follow-up addresses the existing TEST owner processing route
that admits one to four lowercase path segments. Adding exact V2 leaves
does not, by itself, disable the previous V1 path. Even deleting both exact
V2 leaves on rollback would silently re-expose V2 through that regex.
Never rely on the absence of an exact location in this layout.

- Use actual current read-only trusted TEST snapshots: full Gateway spec,
  canonical Nginx include, verified managed host, fixed existing TEST IP, exact
  internal owner callback/cookie isolation and same loopback Visual runtime.
  Confirm the deployed normal owner UI already has its matching owner marker
  capability, and privately verify effective Nginx includes, method forwarding,
  fallback security and the marker file scope. The renderer emits only review
  data and grants no access. Synthetic tests alone do not qualify live inputs.
- Review/pin all before, dark and active byte pairs. Install the fail-closed
  dark pair first: two explicit V1 plus two explicit V2 404 locations. This
  temporarily changes only admission to the previously disabled clear feature.
  Keep Graph readiness off. Standard operator configuration validation and
  locked expected-old/new compare-and-swap are required for either plane;
  don't publish blindly if snapshots or installed archive/state moved.
- Once no unsupported TEST recorder can be affected and ordinary graph/queue
  and event path are still healthy, replace only the explicit V2 denies with
  fixed POST prepare and PUT action locations; the V1 deny remains. Publish
  only the two exact Gateway routes, preserving every other path/method.
  Coordinate with the established application release operator; apply no
  readiness while another Graph activation, restart or SSE proof is pending.
- Verify the existing owner and rejected anonymous routes. Install only the
  separately reviewed purpose-limited Graph readiness flags and same lane
  private read-only key. On the actual owner TEST UI confirm scope and the
  stage confirmation, then cancel. For deeper verification use disposable
  fixtures only. Never invoke the clear action on the shared TEST/PROD
  database even if it mostly contains synthetic data.
- Rollback uses the pinned dark state: restore V2 explicit denies and verify
  them before dropping exact Gateway V2 paths; otherwise the generic Gateway
  route can still forward them. Keep V1 explicit denies across all rollbacks.
  Remove only the separate readiness drop-in/credential with the ordinary
  service lifecycle afterward, preserving Graph login, queue reads, SSE,
  source controls and release pointers. Never restore stale unrelated routes
  or current data. A changed config requires a fresh independent rollback plan.
