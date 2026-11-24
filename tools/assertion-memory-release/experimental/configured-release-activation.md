# Shared release activation (integration criteria)
 
The existing release producers build exactly one archive per GitHub Release.
The shared privileged entry point uses the successful producer completion on
protected main. It independently looks up the event's run through GitHub, and
selects exactly one published, immutable, non-prerelease by authoritative tag
and namespace with an exact matching producer run. Read-only PR runs, failed
runs, forks, untrusted workflow paths and attempts other than 1 are not
deployment triggers. The old manually dispatched deploy workflows remain
available during initial replay and rollback.

A separate workflow_dispatch input is an operator replay mechanism for
already released tags, not the end-user interface. No IDs are needed in
the ordinary release-by-tag path. No branch/tag-supplied code is run with
deployment secrets; the controller is checked out at protected main.

## One runtime graph

1. **Build** (read only): resolve original, immutable source. Set profile,
   release and artifact identity, and exact TEST and PROD lane environments
   and concurrency keys from the approved repository profile. Never execute
   artifact-supplied code in the controller.
2. **TEST**: require Build, enter TEST environment, then shared verifier
   checks exact source, approval and raw zip digest. Call the single
   registered environment-specific receiver. The operation must smoke
   actual ingress and return its live identity. Generic evidence is written
   only after a successful smoke and preserved as the single same-run artifact.
3. **PROD**: require TEST and Build, enter PROD environment for the ordinary
   review button. Independently re-fetch source and raw digest. Pull the
   same-attempt successful TEST job and stored proof from Actions, checking
   the record matches exact immutable Build identity before calling the
   registered PROD receiver. Smoke production ingress with the original
   identity. A missing or expired artifact, unfetched independent evidence,
   or failed receiver must stop on the affected stage, never use a fallback.

Staging profile config supplies source producer and tag format, destination
environments/receive methods, and canonical payload type. Profile boundaries,
immutable source verification, stage approval, receipt generation and
verification are shared. The payload-specific receiving commands can verify
format, execute the actual promotion and smoke the stage.

Before flipping the legacy path, prove both supported profiles yield exactly
Build -> TEST -> PROD and the receipt origin checks cover actual GitHub job
names and ordinals. Replay exact available Visual v0.1.0 and Base v0.1.1
without changing published Release assets. Validate one real TEST and then
PROD for each; compare previous digest/pointer and current separately; retain
old path until both pass.
