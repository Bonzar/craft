# TEST graph-node and device activation package (source only)

One package composes the canonical TEST graph/node and device preflights,
immutable request/candidate receipts, and fixed TEST transaction interface.
Graph and devices are independent; neither is enabled by releasing this
package. There is no CLI, socket, host adapter, or workflow. No change to
Base startup, Visual, public routing, privileged access, or PROD.

The existing protected TEST release hook handles release lifecycle and
authenticated health for the two services, but does not recognize feature
receipts or prepare/install these exact profiles. A future approved trusted
adapter must explicitly implement the package interface with the same TEST
deploy lock, lane-bound immutable review verifier, durable claim/reconcile
semantics, safe no-replace paths, exact identity and restart checks. No
caller-supplied receipt, boolean, program, path, or credentials carry authority.
Readback and non-locking health run under the writer lease; ordinary owner
request smoke and synthetic mutations run only after releasing it. Pending
or uncertain effects are reconciled from protected observed state, never
automatically attempted again.

A graph inverse removes only the exact installed graph flag. A device inverse
removes only the exact marker and keeps the stage registry and audit data.
Use the existing same-origin REST carrier unchanged; require independent
read-only proof that it preserves the owner cookie, Origin, body and CSRF.
Fixtures contain no actual accounts, credentials, production paths or data.
