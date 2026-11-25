# Separate Monium Traces collectors

Use one independently configured instance per lane. Initially start TEST only.
The Base app receives no cloud credential. Create a distinct **Traces write-only
Application Key** for each lane, in that lane's actual Monium project. The
[Monium OTLP transport](https://yandex.cloud/ru/docs/monium/collector/otlp-protocol)
requires TLS, `Authorization: Application-Key …` and `x-monium-project` headers.
Do not share projects or credentials with the other lane or journal logs.
See [Monium application keys](https://yandex.cloud/ru/docs/monium/security/application-keys).

The shared loopback is not a security boundary against another local UID. Use
independent collector users and credentials; if other local principals are
not trusted, restrict each receiver to its matching Base service using the
host's UID/network ingress policy. Resource filters do not prevent deliberate
forgery of labels or metadata by a sender that can access the socket.

## Offline validation on Mac or Linux

Use official [Collector Contrib v0.166.0](https://github.com/open-telemetry/opentelemetry-collector-releases/releases/tag/v0.166.0).
Download the matching tarball (e.g. `otelcol-contrib_0.166.0_darwin_arm64.tar.gz`
or `otelcol-contrib_0.166.0_linux_amd64.tar.gz`) and its `.sha256` companion
into scratch. Compare the output of `shasum -a 256 "$archive"` (Mac) or
`sha256sum "$archive"` (Linux) to the hash in the release companion file; the
companion contains the digest without a filename. Extract only the verified
`otelcol-contrib` binary.

```sh
python3 tools/assertion-memory-hosted-mcp/monium-trace-collector/smoke.py /absolute/path/to/otelcol-contrib
```

The smoke validates both original configs, then uses dummy credentials,
ephemeral loopback listeners and a local sink only. It tests exact lane
selection and removal of synthetic sensitive attributes, not cloud ingestion.

## Runtime paths

Use dedicated, unprivileged accounts `assertion-otel-test` and
`assertion-otel-prod` (the latter only at PROD rollout), without login shells.
Install the verified executable at `/usr/local/bin/otelcol-contrib`, root-owned
and not writable by services. The approved Base archive contains these exact
non-secret operator files under
`tools/assertion-memory-hosted-mcp/monium-trace-collector/`, covered by the
archive's verified SHA-256 and file manifest. Verify the original archive
bytes and file manifest independently before root copies these files;
do not point the root-managed collector at the mutable TEST user release
symlink or let TEST user-owned paths provide executable root config.
The Base receiver stages and qualifies the assets but deliberately only has
authority to restart its own graph/MCP user units. It does not install or
automatically activate system collectors or manage their credentials.

Install verified files into root-controlled paths:

| Repo file | Host path |
|---|---|
| `common.yaml` | `/etc/assertion-monium/traces-common.yaml` |
| `test.yaml` | `/etc/assertion-monium/traces-test.yaml` |
| `prod.yaml` | `/etc/assertion-monium/traces-prod.yaml` |
| `assertion-monium-traces@.service` | `/etc/systemd/system/assertion-monium-traces@.service` |

Populate `/etc/assertion-monium/test-traces.env`, owner root, mode 0600,
via the approved private secret workflow. Exact variable names:

```text
MONIUM_TRACES_PROJECT=<TEST Monium project ID>
MONIUM_TRACES_APPLICATION_KEY=<TEST write-only Traces application key>
```

Do not pass real secrets in CLI args, paste in chat, put them into Base
settings or print expanded config. Verify no outbound TLS-intercepting proxies
are set for the service. Only the matching collector process receives its
private EnvFile. Exact TEST process command under that systemd unit:

```sh
/usr/local/bin/otelcol-contrib --config=/etc/assertion-monium/traces-common.yaml --config=/etc/assertion-monium/traces-test.yaml
```

After approval, reload systemd units and start only
`assertion-monium-traces@test.service`. Check its listener is
`127.0.0.1:14318`. Follow the [Base TEST opt-in](../MONIUM-GENAI-TRACES.md)
and verify one fictional query with a model call lands only in the TEST
project. For the repository's standard canonical TEST user unit, the
non-secret opt-in belongs in the root-owned user-manager drop-in
`/etc/systemd/user/assertion-test-deploy-mcp.service.d/50-monium-traces.conf`.
Verify the actual TEST unit name/fragment first, reload its user manager,
then let a normal TEST release restart the MCP unit. The drop-in and the
independent collector configuration are outside the release's `current`
symlink, so an ordinary deploy/rollback does not overwrite them. No change
to the application archive after signing, production unit, or data paths
is needed. PROD is not enabled by this procedure. Its later rollout has a
separate `prod-traces.env`, project, key, service and port `24318`.

Rollback: remove the Base TEST opt-in, restart that service normally, then
stop the TEST collector. Do not touch PROD or the journal logs pipeline.
Queued traces are in memory only, with bounded timeout/retries; no disk spool.
