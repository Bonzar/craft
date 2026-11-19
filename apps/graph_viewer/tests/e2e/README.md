# Independent viewer QA

The viewer API contract lives in `../../docs/contract.json`. This folder contains test tooling. It does not start a server, alter core source,
configure remote access or open a real database. Direct SQL requires the new
synthetic output directory marker. Browser runs require an explicit readiness
flag and a synthetic manifest; private real-source screenshots are refused.

Generate the full control source with Python 3.12:

```sh
python scripts/fixture.py --directory /private/tmp/graph-viewer-qa/NEW-FIXTURE
```

Run generator/oracle preconditions from `apps/graph_viewer`:

```sh
python -m unittest discover -s tests/e2e -p 'test_*.py' -v
```

These small precondition tests prove the fixture tools, not viewer scalability.
The full default output is exactly 100000 nodes and 1000000 edges; its manifest
contains independent source digests, known case IDs and temporal cutoffs.
Generation streams batches of 1000 edge rows. Output directories are never
overwritten. Appending live changes is allowed only to a separately marked
synthetic fixture, with its initial full-size evidence retained.

After root signals readiness, configure environment without exposing passwords
in shell arguments or logs:

- `GRAPH_VIEWER_QA_URL`: owned service origin.
- `GRAPH_VIEWER_QA_PASSWORD`: owner credential, never a frontend public env.
- `GRAPH_VIEWER_QA_MANIFEST`: synthetic fixture manifest absolute path.
- `GRAPH_VIEWER_QA_PYTHON`: optional Python executable for the fixture oracle (defaults to `python3`).
- `GRAPH_VIEWER_QA_READY=1`: explicit integration-ready signal.
- `PLAYWRIGHT_BROWSERS_PATH`: isolated downloaded browser directory.

The HTTP black-box audit uses only `/api/v1` for the viewer, comparing results
to marked synthetic SQL. It checks all private read-route denials, full binary
records/endpoints, raw temporal fields, Unicode edge fragments, deep source
focus, historical counts, two independent owner bookmark sessions, CSRF and
source immutability. Its timings are HTTP timings, not browser paint claims.

```sh
python tests/e2e/public_audit.py --base-url "$GRAPH_VIEWER_QA_URL" \
  --fixture-manifest "$GRAPH_VIEWER_QA_MANIFEST" --output /private/tmp/graph-viewer-qa/http-report.json
```

From this directory, `npm ci` then `npm run list` validates discovery without
launching a browser. `npm run test:chrome` uses installed Chrome; individual
`--project=firefox`, `webkit`, `mobile-chromium-emulation` and
`mobile-webkit-emulation` runs use pinned downloaded engines. Workers=1 avoids
parallel GPU/memory contention on the 8GB host. No user profile is reused.
Traces/videos are disabled to avoid recording credentials. Screenshots contain
explicitly synthetic source only.

The browser harness checks actual GL draw submissions/buffer sizes in addition
to manifest counts and read-only application diagnostics. It targets first and
last stable source IDs through actual star picking, measures rendered camera
motion and preserves screenshots. Actual WebKit is not desktop Safari. Mobile
viewports are not physical mobile Safari/Chrome, and local loopback is not a
mobile-internet acceptance run. Record those gates as NOT_RUN until observed.

All six approved performance thresholds remain mandatory: complete usable map
≤10s, visible interaction feedback ≤200ms, orbit/zoom ≥30fps, first card text
≤1s, first search results ≤2s, ready source live change to visible update ≤2s.
Data/index preparation is measured separately. Stable acceptance conditions
are ≥10Mbps and ≤100ms RTT; bulk changes/offline recovery are separate cases.
Run the browser suite against the synthetic fixture to measure these thresholds.
