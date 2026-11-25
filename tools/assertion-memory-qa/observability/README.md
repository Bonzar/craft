# Memory query timing in Yandex Monium

The application writes structured timing to its local systemd journal. It does
not make telemetry HTTP calls or wait for Monium. One optional Fluent Bit 4+
collector per lane forwards the allowlisted timing fields as OTLP logs.

## Install only after a synthetic TEST smoke

- Use a private Monium project and a dedicated service-account API key scoped
  to `yc.monium.logs.write` with the project's `monium.telemetry.writer` role.
  Store the key through the host's approved secret mechanism; never in git,
  command-line arguments, application env files or log output. Cloud ingestion
  is billed per volume. No metrics, traces, alerts, extra public listener or
  project-wide journald export are configured by this template.
- Pin the reviewed Fluent Bit package version. Set `MEMORY_MONIUM_CONFIG` to
  the read-only installed config directory and `MEMORY_MONIUM_STATE` to a
  uniquely scoped writable collector-state directory. Set
  `MEMORY_MONIUM_LANE` to `test` or `prod`,
  `MEMORY_QUERY_UNIT` to that lane's exact verified unit,
  `MONIUM_PROJECT` to the Monium project ID, and supply `MONIUM_API_KEY` as a
  secret. Verify the actual PROD unit on the host; never infer it from TEST.
  A first run tails new records only; it does not backfill the system journal.
- Validate configuration with the exact target binary and a dedicated
  synthetic journal unit. A line with canary fields like `prompt`, raw
  `MESSAGE`, authorization or a non-timing marker must not reach Monium.
  A valid timing line should show a query ID, lane, stage, status and duration.
  Test recovery from exporter unavailability separately. Do not change Base
  application service or VM routes just to enable forwarding.
- Stop/disable only that lane's collector to roll back cloud export. The Base
  timing journal continues locally. The queue on disk is bounded per output.

In Monium Explore → Logs filter by `event=memory_query_timing`, lane and
`trace_id` (the interactive MCP job ID), then sort by time. Start with
`queue_wait` and `query_total`. Use `run_id` / `call_id` to follow search,
draft generation or verification into each provider attempt. A long
`provider_cli` contains time inside the Codex subprocess; it does not by
itself separate inference from provider routing, network or an internal retry.
Explicit retry signals are recorded when available; their absence is not proof
that the provider did not retry. `vm_http_submission` covers the VM API, not
Sites, the API Gateway or the client's poll interval.

References: [OTLP endpoint and authorization](https://yandex.cloud/en/docs/monium/collector/otlp-protocol),
[Fluent Bit](https://yandex.cloud/en/docs/monium/collector/fluentbit),
[Logs Explorer](https://yandex.cloud/en/docs/monium/logs/logs-explorer),
[pricing](https://yandex.cloud/en/docs/monium/pricing).
