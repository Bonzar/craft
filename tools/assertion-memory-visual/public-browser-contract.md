# Static Visual / Base public boundary

The browser keeps calling its own origin. Base should implement these existing
public endpoints, not expose its service-to-service paths. This contract does
not move the browser's owner session, CSRF, or operation proof into static code.

| Surface | Existing browser calls |
| --- | --- |
| Session | `GET/POST/DELETE /api/v1/session`; existing cookie path, Secure, SameSite, CSRF and error behavior. |
| Graph | `GET /api/v1/capabilities`, `/graph` with the current selector, and immutable URLs from that manifest for revisions, binaries, topics and ordinal cards. Stable `/nodes/{id}`, `/edges/{id}`, `/sources/{id}`, `/overviews/{id}`, and `/search` remain revision-scoped; bookmarks use existing GET/PUT/DELETE. |
| Live | Fallback `GET /api/v1/events`; dashboard channel uses `?channel=dashboard`. Approved TEST direct path still starts with owner `POST /api/v1/events-ticket` on the same origin, then sends only that one-use ticket to the configured exact HTTPS stream. Epoch fencing and foreground revalidation remain unchanged. |
| Queue reads/mutations | `GET /api/v1/capabilities?queue_status=1&limit=…&offset=…`, `/api/v1/processing/sessions` and existing prepared operation paths. Queue pause keeps `POST /processing/pause/prepare` then `PUT /processing/pause`; source preview/removal use `/processing/items/source-preview` and `/processing/items/remove`. |
| Clear | Existing owner-gated `POST /api/v1/processing/database/clear-v2/prepare` and `PUT /api/v1/processing/database/clear-v2`. Preserve stage/instance/revision checks, typed confirmation and receipt handling. No change to whether production exposes capability. |

Prepared operation proof stays in the existing response `csrf_token` field
and is sent back as `X-CSRF-Token`, not stored in or supplied by static assets.
If pause requires prepared proof, keep the existing standalone capability
projection as well: `standalone_pause:true` with the legacy direct
`processing_control.can_change` left false. Setting the latter true selects
the legacy session-CSRF request path in current Visual.

## Cutover and cleanup order

1. Install the matching Base-owned public handler without changing the current
   production route. Verify its owner-authenticated session, previous response
   shapes, lane isolation, query/selector forwarding, errors, and read-only
   negatives. Keep internal machine paths unexposed.
2. On protected TEST, route only the existing public `/api/v1` owner surface
   to Base while `/visual`/asset URLs still deliver the unchanged static archive.
   Check login/logout, graph snapshots and live foreground transition, queue
   paging/controls and prepare/confirmation without a new browser credential.
   The direct TEST stream origin and narrow proof stay as before. A real clear
   is a separate explicitly authorized acceptance exercise.
3. Prepare a protected route-only rollback back to the exact prior Visual API
   handler. Keep that handler and its available release image through verified
   TEST and separate authorized PROD equivalent; do not remove it based on
   static package tests alone. Confirm both successful owner requests and
   absence of traffic/services relying on the old handler after cutover.
4. Only then decommission the old Visual backend and retire its runtime
   companion packaging if no remaining route needs it. The current packed
   deployment still contains and depends on that companion; this document and
   the frontend contract test do not remove or disable it.
