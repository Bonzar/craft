# Additive binding source, uninstalled

These are reviewable pieces for the already authorized existing OIDC and
separately protected root release machinery. They do not install services
or extend existing role grants; native v1/v2 modes remain unchanged.

- Configured terminal admission verifies the same RS256 issuer and existing
  actor/workflow/env/run conditions, with a distinct exact audience over
  tag/archive/manifest/mode. Existing one-use gate must validate and spend
  the precise nonce/run before the separate file CAS; consuming without
  successful durable record must deny (no forwarding or nonce replay).
- Authorizer-owned state derives prior from independently verified initial
  active artifact; no deployment-request-supplied predecessor or phase.
  Existing authorizer may answer one fixed read-only deploy-UID pair
  message, returning no credential or session. Root terminal publication
  still needs independently observed native phase and process scope.
- Guard binding preserves original fixed same-user restart semantics for
  legacy, uses one verified active package and fixed authorizer socket to
  select sidecar when exact policy and archive allow, and requires old
  Graph inactive before starting Base. It does not replace/alter the root
  hook from this checkout. No transition EX lock across browser UI smoke.
- Edge observation is from current provider/operator. No provider call is
  implemented here. Exact inverse ordering Gateway then Nginx, refuse
  foreign/different generations, verify old release and route as a pair.
  Do not deploy a second public carrier or alter app data.

Untracked live guard/hook source remains only with authorized operator;
separately reviewed source transform/head and exact immutable ops/control
approval are mandatory before using these functions. Current live policy,
authorizer dispatcher, root holder, scoped startup and ingress unchanged.
