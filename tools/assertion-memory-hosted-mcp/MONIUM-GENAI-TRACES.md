# Payload-free Monium LLM tracing

This is independent of the existing journal timing export. It sends standard
[OTLP/HTTP JSON](https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding) spans
to a **lane-local collector**, not directly to Monium. Hosting, cloud destination,
credentials, and transport to Monium belong to the collector/operator. See the
[collector configuration and validation runbook](monium-trace-collector/README.md).

## TEST opt-in

1. Keep PROD's service environment unchanged. Configure a TEST-only OTLP/HTTP
   receiver listening on `127.0.0.1:14318` accepting `POST /v1/traces`, with an
   independent TEST export pipeline, Monium project and write-only credential.
   Confirm that it forwards only the TEST service and lane. Do not reuse the
   receiver, project or credential from PROD.
2. Use the normal Base release flow to deliver the code. Enable only in the
   trusted TEST Base HTTP service environment (systemd service drop-in, not a
   client setting):

   ```ini
   [Service]
   Environment=ASSERTION_MEMORY_MONIUM_TRACE_ENV=test
   ```

   Restart from the normal TEST service procedure. The environment value must
   exactly match the private host's independently configured lane or the
   exporter stays off. Unset and restart to disable. The app contains no
   adjustable remote endpoint, auth headers, cloud credential or SDK/collector
   dependency.
3. Submit one synthetic TEST query that actually invokes Codex. In TEST Monium
   Traces, search for `service.name="assertion-memory-test"` and
   `gen_ai.operation.name="chat"`. Check that `deployment.environment.name=test`
   is present, the trace ID matches the app journal's opaque job ID, and the
   provider spans have their expected stage parents. Confirm that no prompt,
   response, tool data, identity/session attribute or raw error is present.
   Queries that only reuse an answer may produce no provider span. For search
   and other non-GenAI spans, open **Show full trace** in the LLM view.

| Lane | Exact app opt-in | Reserved loopback receiver | Resource |
|---|---|---|---|
| TEST | `ASSERTION_MEMORY_MONIUM_TRACE_ENV=test` | `127.0.0.1:14318` | `assertion-memory-test`, `test` |
| PROD | `ASSERTION_MEMORY_MONIUM_TRACE_ENV=prod` | `127.0.0.1:24318` | `assertion-memory-prod`, `prod` |

Do not enable PROD until a separate PROD project/credential/receiver is
reviewed and the TEST verification completes. Labels differentiate views;
they are not an authorization boundary. On a shared host, protect collector
listeners from other local principals if local cross-service forgery is in
scope.

## Data and failure behavior

The resource only identifies the service and lane. Normal spans carry their
stage, timing, terminal outcome, and where available internal opaque run/call
IDs. The job root includes durable queue wait in `memory.queue_wait_ms`.
The `openai.chat` provider spans use the
[GenAI attributes supported by Monium](https://yandex.cloud/ru/docs/monium/traces/llm/manual_instrumentation#obyazatelnye-atributy-spanov-generacii).
Requested model is distinct from the actual model reported by a completed
provider event. Unknown actual model or token counts are omitted, not
substituted with requested model or zero. Cached and reasoning counts are
subsets of reported input/output. Payload attributes, message events,
tool data, raw exception messages, scopes and caller identities are absent.
Per the [Monium LLM trace UI docs](https://yandex.cloud/ru/docs/monium/traces/llm/traces),
spans with no message text remain visible but collapsed by default.

The app uses a 512-entry memory queue, batches up to 64 entries, and posts
over loopback from a daemon thread only. The app never waits on the collector:
a full queue, export failure or process restart may drop spans, with no
retry/replay from the app. Journal timing remains independent. Trace durations
cover service and provider boundaries the application can actually observe,
not unreported provider back-end compute, external Sites network or browser time.
