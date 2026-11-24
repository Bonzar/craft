# Functional hosted MCP from one canonical release

This is a source candidate, not a deployment. Use the same policy, ASGI and
Worker bytes in TEST/PROD. All origins, release identity, runtime deadlines,
operator-approved capabilities, owner verification and separate credentials
are specified by protected environment profiles only.

Functional source reference for the added search is merged #237 (f3467b3d).
The local .review.json file is an OFFLINE compatibility input extracted from
exact function signatures using SDK 1.29.0; it must never be accepted as
release authority. Use compile_functional_release.py on the actual pinned
Base release archive's mcp-tool-schema.json after independently verifying
archive, checksum and the requested Base release pin. Current deployment
SDK must be exactly 1.30.0. Build both read and full catalogs from the same
verified manifest. Keep the existing v0.1.1 catalog and v22 transport until
the new branch/version is approved; the 17-entry old release cannot advertise
search or use the new catalog, even if the name is added to a Worker.

* Baseline read adds canonical memory_search (query, scope?, session_id?,
  limit). It does not call memory_query or start a model.
* Existing effects keep canonical capture_message/process only.
* memory_query is another effect (can generate and publish). It uses the
  separate per-lane GENERATE_ENABLED feature flag and a server-authorized
  generate capability, also requiring the effect credential. It retains
  the same canonical query handler, no new pipeline, and has a separate
  concurrency gate. Do not auto-retry ambiguous effects.
* Do not copy the old short read/ingest ingress budget to query. Review the
  actual native MCP client, Sites, Yandex Gateway, Nginx, VM upstream and
  provider deadlines; bound to a reviewed compatible budget before setting
  the two profile timeout values and enabling explicit generation. Do not
  falsely claim the client can wait for all model operations. The current
  short 6-second effects ingress is not query-ready.
* Owner-only authenticated navigation (not unbound probes) must verify that
  exact new release has the functional catalog. Then native read-only search
  against existing approved TEST data or an empty result, canonical status
  and existing authorized capture status/receipt. No seed/mocks or duplicate
  real message; capture and query effects are not necessary just to prove
  authority routing. Only then qualify equivalent PROD profiles independently.
