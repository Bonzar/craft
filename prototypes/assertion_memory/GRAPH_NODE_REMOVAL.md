# Owner: remove one graph thought
This private, loopback-only API uses the existing owner write credential and exact
path/lane/body signature. Each path has a distinct proof. Trusted Visual must
validate its browser owner session and CSRF before signing; do not forward a
browser-supplied identity. Neither route is public or mounted by Sites.

1. PUT `/api/v1/graph-nodes/remove-preview`:
   `{"thought_id":"thought-…","thought_version":3,"expected_revision":"00000000000000000000000000000000"}`.
   This performs no DB writes. It returns stage, thought_id/thought_version, scope
   (database generation), source_revision (canonical graph), and revision (the
   confirmation challenge). Display the selected item and ask for confirmation.
2. PUT `/api/v1/graph-nodes/remove` with the same stable thought ID and integer
   version, and challenge as expected_revision. Use a fresh proof nonce. A 200
   returns those same identity fields, removed=true, and the **new** canonical
   source_revision. Reconcile by refreshing the graph (Visual's own packed graph
   revision is not the canonical source revision). No optimistic removal on 409.

This withdraws exactly one **current** canonical head from the active graph and
search, including its visible incident edges. Other thoughts, stored exact
historical versions, audit links, evidence and original raw/chat content are not
deleted. No history/ordinal/raw/topic ID, force option, or cascading operation.

Expect typed 409 for changed/missing node, stale confirmation on graph/release or
database generation change, dependent retained thoughts, or unfinished work on
the selected node. No partial commit. Support for materially larger databases
requires an indexed dependency proof: this implementation deliberately refuses
a scan beyond 5,000 stored external versions instead of pretending safety.

## Visual integration
The authenticated owner browser uses POST `/api/v1/processing/graph-nodes/remove/prepare`
and PUT `/api/v1/processing/graph-nodes/remove`. The browser flow binds the returned
one-use confirmation to the same session, lane, thought ID, version, and revision.
The browser cannot reach the private Base route directly. Both lanes use the same
`GraphNodeOwnerBridge` over the pinned loopback Base port. The browser routes and
capability are absent unless a reviewed deployment explicitly injects a bridge
for that exact lane, and the ingress generator defaults off separately. No
credential/value changes, trusted route activation, or database mutation are
included in this candidate.
