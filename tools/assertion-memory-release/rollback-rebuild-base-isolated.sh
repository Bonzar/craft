#!/usr/bin/env bash
# Disabled-source adapter. Run only on a disposable, no-secret GitHub build job.
# Arguments are set by trusted policy, never raw dispatch text:
# exact prepared image digest, verified candidate source tar, new output dir, exact revision.
set -euo pipefail
[[ $# == 4 ]] || exit 2
image="$1"
source_tar="$2"
dest="$3"
revision="$4"
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ "$image" =~ ^[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}$ && "$source_tar" == /* && "$dest" == /* ]] || exit 2
[[ "$source_tar" != *,* && "$dest" != *,* ]] || exit 2
[[ -f "$source_tar" && ! -L "$source_tar" && ! -e "$dest" && ! -L "$dest" ]] || exit 2
mkdir -m 700 -- "$dest" || exit 2
# Use only the previously prepared exact image. Do not execute historical
# install scripts with network egress or stage a user HOME / GitHub token here.
# The image contract is Python 3.12 with pinned dependencies, Node 24, and an
# offline npm cache populated by current trusted tooling for this exact lock.
# Pass no host environment; Docker literal flags create an independent sandbox.
if ! /usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin docker run --rm --pull never \
    --network none --read-only --user "$(id -u):$(id -g)" --cap-drop ALL \
    --security-opt no-new-privileges --pids-limit 100 --memory 3g \
    --tmpfs /work:rw,nosuid,nodev,size=2g,mode=1777 \
    --mount "type=bind,src=$source_tar,dst=/source.tar,readonly" \
    --mount "type=bind,src=$dest,dst=/out" \
    --entrypoint /usr/bin/env "$image" -i PATH=/usr/local/bin:/usr/bin:/bin /bin/sh -eu -c '
        # Git-generated candidate tar from the approved exact commit only.
        cd /work; mkdir src home cache tmp; tar -xf /source.tar -C src
        export HOME=/work/home TMPDIR=/work/tmp npm_config_cache=/work/cache npm_config_userconfig=/dev/null
        cp -R /opt/rebuild-npm-cache/. /work/cache/
        cd /work/src/apps/graph_viewer
        npm ci --offline --ignore-scripts --no-audit --no-fund
        npm run build
        cd /work/src
        python -IB -c "import sys; from runpy import run_module; sys.path.insert(0, \"/work/src\"); run_module(\"prototypes.assertion_memory.release_catalog\",run_name=\"__main__\")" --revision "$1" --output /out/mcp-tool-schema.json
        cp -R /work/src/apps/graph_viewer/web/dist /out/
    ' sh "$revision" </dev/null >/dev/null 2>&1; then
    printf '%s\n' 'Isolated historical compile failed; no consumable rebuild was produced.' >&2
    exit 1
fi
