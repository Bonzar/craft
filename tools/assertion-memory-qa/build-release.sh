#!/usr/bin/env bash
set -euo pipefail
umask 077
root="$(git rev-parse --show-toplevel)"
cd "$root"
sha="$(git rev-parse --verify HEAD)"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]]
dest="${1:?absolute output directory required}"
[[ "$dest" == /* ]]
mkdir -p -- "$dest"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
stage="$work/release"
mkdir -p "$stage"
hosted_runtime=(
  tools/assertion-memory-hosted-mcp/policy.py
  tools/assertion-memory-hosted-mcp/vm_relay.py
  tools/assertion-memory-hosted-mcp/worker.mjs
  tools/assertion-memory-hosted-mcp/compile_functional_release.py
)
git archive "$sha" -- prototypes/assertion_memory apps/graph_viewer "${hosted_runtime[@]}" | tar -x -C "$stage"
# These exact helpers are CI-generated or copied from the QA-only tools directory.
install -m 600 "$root/tools/assertion-memory-qa/smoke_local.py" "$stage/smoke_local.py"
sed -i 's/resolve().parents\[2\]/resolve().parent/' "$stage/smoke_local.py"
install -m 600 "$root/tools/assertion-memory-qa/synthetic_fixture.py" "$stage/synthetic_fixture.py"
install -m 600 "$root/tools/assertion-memory-qa/live-smoke.py" "$stage/live-smoke.py"
install -m 600 "$root/apps/graph_viewer/requirements.txt" "$stage/requirements-graph-original.txt"
grep -v '^fastembed==' "$stage/requirements-graph-original.txt" > "$stage/requirements-graph-lexical.txt"
test -s "$root/apps/graph_viewer/web/dist/index.html"
cp -R "$root/apps/graph_viewer/web/dist" "$stage/apps/graph_viewer/web/"
printf '%s\n' "$sha" > "$stage/REVISION"
# Registered transport schema is compiled from this exact staged candidate.
(cd "$stage"; python3 -B -m prototypes.assertion_memory.release_catalog \
  --revision "$sha" --output "$stage/mcp-tool-schema.json")
test -s "$stage/mcp-tool-schema.json"
printf '%s\n' 'assertion-memory-synthetic-qa-v1' > "$stage/QA_PROFILE"
(cd "$stage"; find . -type l | grep . && exit 1; test -z "$(find . -type f \( -name '*.sqlite*' -o -name '*.db' -o -name '*.pem' -o -name '*.key' -o -name '*.env' \))"; find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > "$work/manifest"; mv "$work/manifest" SHA256SUMS)
name="assertion-memory-qa-$sha.tar.gz"
# Stable file order and metadata independent of build runner UID.
tar --sort=name --mtime='@0' --owner=0 --group=0 --numeric-owner -C "$stage" -cf - . | gzip -n > "$dest/$name"
(cd "$dest"; sha256sum "$name" > "$name.sha256")
# Qualify through the shared receiver without rewriting the immutable bytes.
PYTHONPATH="$root/tools/assertion-memory-release" python3 - "$dest/$name" "$sha" "${hosted_runtime[@]}" <<'PYQUAL'
from hashlib import file_digest
from pathlib import Path, PurePosixPath
import subprocess
import sys
from assembly import inspect
from base_payload import SPEC, read_manifest, validate
path,revision=Path(sys.argv[1]),sys.argv[2]
with path.open('rb') as archive:
    digest=file_digest(archive,'sha256').hexdigest()
def qualify(staged):
    key = validate(staged, revision, digest)
    expected = {PurePosixPath(name) for name in sys.argv[3:]}
    prefix = PurePosixPath("tools/assertion-memory-hosted-mcp")
    manifest = read_manifest(staged)
    actual = {name for name in manifest if prefix in name.parents}
    if len(expected) != 4 or actual != expected:
        raise ValueError("hosted_runtime_membership")
    for name in expected:
        committed = subprocess.check_output(["git", "show", f"{revision}:{name}"])
        if committed != (staged / name).read_bytes():
            raise ValueError("hosted_runtime_source_mismatch")
    return key
inspect(path, digest, spec=SPEC, validate=qualify)
PYQUAL
