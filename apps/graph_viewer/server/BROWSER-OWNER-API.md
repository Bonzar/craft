# Base browser owner API
The public Visual API uses the existing Base Graph listener and the existing
owner credential store and cookie. Asset serving remains outside it. Private
Base/MCP listeners are not the public entry. No new secret is installed.

Initial rollout is opt-in with `--browser-owner-api` on the canonical TEST
Graph profile (exact HTTPS origin, current release tenant, existing trusted
queue projection, release lock, same Graph user and same viewer state).
Do not also set `--queue-control`, private bridge/reset bearer flags, or
additional proxy hosts. Locale/stage is never inferred from browser headers.
The Base domain and browser adapter are shared; a later PROD policy can use
the exact same code after validating its canonical serving and DB identity.
Neither flag is enabled automatically by this source change.
`--browser-owner-clear` separately allows the actual clear operation and
stays off for ordinary deployment until specifically authorized.

Forward browser `/api/v1` to this listener with fixed public Host and only
Cookie, Origin, X-CSRF-Token and ordinary content/stream headers. Do not
forward Authorization, X-Memory-* or client identity assertion headers.
No public mapping to private queue REST, MCP, or another lane. Existing direct
TEST SSE is a narrowly scoped separate path — GET/OPTIONS through the current
direct carrier, ticket bearer only, no Cookie, no private bearer elsewhere.
The same-origin issuer and standard events share ordinary owner sessions.

Browser operation prepare requires authenticated same origin and returns the
current overlay's `csrf_token` action confirmation. PUT uses that opaque
single-use proof; it is bound to the owner session, revision, operation and
item identity. Ordinary session-token routes such as logout, bookmarks and
stream issuer retain their previous session CSRF. For clear, the existing UI
shows one typed phrase, and the backend additionally checks release/DB
generation, Graph readiness and recorder continuity. Prepare performs no
destructive write; commit uses the single existing protected Base transaction.
No ambiguous automatic retry. API responses are private/no-store.

Required activation-only follow-up: the installed legacy loopback release
smoke currently fetches Graph `/` and logs in without an Origin. This adapter
intentionally serves API only and expects exact Origin on login, so use the
ordinary protected, versioned smoke evolution for fixed TEST Origin +
`/api/v1/health` before switching the opt-in flag. Do not add public or hidden
bypasses. Base publisher startup ordering is tolerated: owner login does not
depend on queue projection availability, while queue/control fails closed.
