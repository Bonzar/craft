#!/bin/bash
# Reviewed build-only specimen; do not source from deployment.
set -euo pipefail
[[ $# == 5 ]] || exit 2
src="$1"; deps="$2"; result="$3"; expected_patch="$4"; toolchain="$5"
[[ "$expected_patch" == 3.12.16 && "$src" == /* && "$deps" == /* && "$result" == /* && "$toolchain" == /* ]] || exit 2
[[ -d "$src/apps/graph_viewer" && -d "$deps/wheelhouse" && -d "$deps/npm-inputs" && ! -e "$result" && ! -L "$result" ]] || exit 2
[[ "$(git -C "$src" rev-parse HEAD)" == 710cf557d29cf91e6ab1550d27e525817df96588 ]]
[[ -z "$(git -C "$src" status --porcelain --untracked-files=all)" ]]
[[ "$(uname -m)" == x86_64 ]]
[[ -d "$toolchain/bin" && ! -L "$toolchain" ]]
[[ "$("$toolchain/bin/python3" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')" == "$expected_patch" ]]
[[ "$("$toolchain/bin/node" --version)" == v24.22.3 ]]
[[ "$(PATH="$toolchain/bin:/usr/bin" "$toolchain/bin/npm" --version)" == 11.19.1 ]]
here="$(cd "$(dirname "$0")" && pwd)"
# Trust only exact pre-acquired files; do not read any candidate dotenv/npmrc.
python3 "$here/verify_035_material.py" "$deps"
mkdir -m 700 "$result"
mkdir -m 700 "$result/source"
git -C "$src" archive --format=tar 710cf557d29cf91e6ab1550d27e525817df96588 \
  -- apps/graph_viewer prototypes/assertion_memory > "$result/source.tar"
python3 "$here/verify_035_material.py" --source "$result/source.tar"
(
  cd "$result/source"
  tar --extract --file ../source.tar --no-same-owner --no-same-permissions
)
# Network namespace plus an explicit minimal filesystem. No checkout .git,
# HOME, runner credentials or deploy secrets mounted. Root is read-only
# except the private scratch build directory and tmpfs. No fallback.
unshare --user --map-root-user --net -- bwrap --unshare-user --unshare-pid --unshare-uts --unshare-ipc --die-with-parent --new-session --clearenv \
  --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
  --ro-bind "$toolchain" /toolchain --ro-bind "$deps" /input --ro-bind "$here" /control \
  --bind "$result" /out --tmpfs /tmp --proc /proc --dev /dev \
  --setenv PATH /toolchain/bin:/usr/bin --setenv HOME /out --setenv LC_ALL C \
  --setenv TZ UTC --setenv PYTHONNOUSERSITE 1 --setenv PYTHONDONTWRITEBYTECODE 1 \
  /bin/bash --noprofile --norc -c '
set -euo pipefail
src="$1"; deps="$2"; out="$3"; here="$4"
python3 -m venv "$out/venv"
"$out/venv/bin/python" -m pip install --no-index --disable-pip-version-check --only-binary=:all:   --require-hashes --find-links="$deps/wheelhouse" -r "$here/base-035-python-full.lock"
mkdir -m 700 "$out/npm"
for p in "$deps"/npm-inputs/*.tgz; do
 npm cache add --cache "$out/npm" --offline --ignore-scripts --no-audit --userconfig=/dev/null "$p"
done
cd "$src/apps/graph_viewer"
npm ci --cache "$out/npm" --offline --ignore-scripts --no-audit --no-fund --userconfig=/dev/null
# Direct vetted Vite entry point, not arbitrary npm package lifecycle hooks.
node node_modules/vite/bin/vite.js build
cd "$src"
"$out/venv/bin/python" -m prototypes.assertion_memory.release_catalog  --revision 710cf557d29cf91e6ab1550d27e525817df96588 --output "$out/mcp-tool-schema.json"
' build-only /out/source /input /out /control
python3 "$here/verify_035_material.py" "$deps" "$result/source/apps/graph_viewer/web/dist" "$result/mcp-tool-schema.json" > "$result/content-witness.json"
