#!/bin/bash
set -euo pipefail
[[ "$(id -u)" != 0 && "$(uname -m)" == x86_64 ]]
# Assert non-loopback interfaces and IPv4 default routing are absent.
[[ "$(< /proc/net/dev)" != *eth0* ]]
awk -F: 'NR>2 { gsub(/[[:space:]]/,"",$1); if ($1 != "lo") exit 1 }' /proc/net/dev
awk 'NR>1 && $2 == "00000000" {exit 1}' /proc/net/route
[[ ! -S /var/run/docker.sock ]]
grep -Eq "^NoNewPrivs:[[:space:]]+1$" /proc/self/status
grep -Eq "^CapEff:[[:space:]]+0+$" /proc/self/status
mkdir -m 700 /out/src /out/cache
tar --extract --file=/input/source.tar --directory=/out/src --no-same-owner --no-same-permissions
mapfile -t py < <(find /toolchain/python -maxdepth 6 -path '*/bin/python3.12' -type f)
[[ "${#py[@]}" == 1 ]]
export PATH="/toolchain/node/node-v24.22.3-linux-x64/bin:$(dirname "${py[0]}"):/usr/bin:/bin"
[[ "$("${py[0]}" -c 'import sys,sqlite3,ctypes;print(".".join(map(str,sys.version_info[:3])))')" == 3.12.16 ]]
[[ "$(node --version)" == v24.22.3 && "$(npm --version)" == 11.19.1 ]]
"${py[0]}" -m venv /out/venv
/out/venv/bin/python -m pip install --no-index --disable-pip-version-check \
  --only-binary=:all: --require-hashes --find-links=/input/wheelhouse -r /python-full.lock
for f in /input/npm-inputs/*.tgz; do
  npm cache add --cache /out/cache --offline --ignore-scripts --no-audit --userconfig=/dev/null "$f"
done
cd /out/src/apps/graph_viewer
npm ci --cache /out/cache --offline --ignore-scripts --no-audit --no-fund --userconfig=/dev/null
node node_modules/vite/bin/vite.js build
cd /out/src
/out/venv/bin/python -m prototypes.assertion_memory.release_catalog \
  --revision 710cf557d29cf91e6ab1550d27e525817df96588 --output /out/mcp-tool-schema.json
