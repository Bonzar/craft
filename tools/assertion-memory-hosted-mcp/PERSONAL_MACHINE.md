# Personal machine access

The machine API is separate from the Sites MCP relay. A valid machine key has
the same full canonical Base tool catalog as every other enrolled key in that
lane. There are no access levels or per-tool permissions. A key cannot access
the opposite lane or Sites MCP. It never grants shell, administration or
release permissions. Keep it limited to personal trusted installations.

1. On the device, generate 32 random bytes and store their lowercase hexadecimal
   encoding (64 characters) in the device keychain or an owner-only file. Do
   not print or send the token in chat, arguments, logs, or release material.
2. Compute SHA-256 over the **64 ASCII hex characters**, not decoded bytes
   and not a line ending. Register only its 64-character lowercase digest
   under a human ID in root-managed
   `/etc/assertion-release/{test,production}/private-operations.json` as
   `"machine_clients": {"personal-mac": "<digest>"}`. Generate a different
   token for each lane and installation. Missing or empty map is disabled.
3. Qualify the scoped hashed key and exact authorized TLS ingress route for
   that lane; reuse the already release-managed Base HTTP listener. Preserve
   existing Sites route/auth settings. Runtime reads the protected profile at
   startup; apply/reload only as part of the authorized release operation.
   Remove a client ID for revocation; rotate by replacing its digest and
   reloading the scoped service. There is no issuance endpoint.

Send `POST /memory-api-test` or `POST /memory-api-prod` with one
`Authorization: Bearer <token>` and `Content-Type: application/json`.
A catalog request is `{request_id, operation:"catalog", arguments:{}}` (UUID
request ID). To invoke, send the same envelope plus
`expected:{release,generation,catalog_sha256}` copied from the catalog. The
machine capability is always `machine`. On stale identity, fetch a new
catalog for subsequent requests; never automatically repeat an ambiguous
effect. Missing/wrong credentials fail before dispatch. The endpoint holds the
existing release lock shared after valid authentication; release activation
holds it exclusively.
