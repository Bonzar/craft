#!/usr/bin/env bash
set -euo pipefail
umask 077
root="$(git rev-parse --show-toplevel)"
cd "$root"
# This runs in PR validation and in the original immutable Release build;
# no deployment workflow/legacy signed producer prelude changes.
python3 -B -m unittest prototypes.assertion_memory.dual_http_test.Dual.test_personal_machine_full_registry_independent_of_sites_key
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
  tools/assertion-memory-hosted-mcp/private_host.py
  tools/assertion-memory-hosted-mcp/compile_functional_release.py
)
# Only exact non-secret operator files, not arbitrary contents of the hosted
# tree or a runtime key/config directory. The receiver does not execute them.
monium_operator_assets=(
  tools/assertion-memory-hosted-mcp/monium-trace-collector/assertion-monium-traces@.service
  tools/assertion-memory-hosted-mcp/monium-trace-collector/common.yaml
  tools/assertion-memory-hosted-mcp/monium-trace-collector/test.yaml
  tools/assertion-memory-hosted-mcp/monium-trace-collector/prod.yaml
)
hosted_release_files=("${hosted_runtime[@]}" "${monium_operator_assets[@]}")
# Committed application modules, not the repository/test/web source trees.
# Pin the two dependency lists explicitly and retain the legacy QA graph server.
application_runtime=(
  prototypes/assertion_memory/requirements-mcp.txt
  prototypes/assertion_memory/requirements-runtime.txt
)
while IFS= read -r -d '' path; do
  case "$path" in
    */tests/*|*/test_*|*_test.py|*/experiments/*|*/offline_test_process.py) ;;
    *.py) application_runtime+=("$path") ;;
  esac
done < <(git ls-tree -rz --name-only "$sha" -- prototypes/assertion_memory apps/graph_viewer/server)
committed_runtime=("${application_runtime[@]}" "${hosted_release_files[@]}")
git archive "$sha" -- "${committed_runtime[@]}" | tar -x -C "$stage"
# Exact root-level entry points consumed by the installed PROD prepare hook.
# These operate only on the hook's temporary SQLite fixture, never the service DB.
prod_preflight=(synthetic_fixture.py smoke_local.py)
for helper in "${prod_preflight[@]}"; do
  git show "$sha:tools/assertion-memory-qa/$helper" > "$stage/$helper"
done
git show "$sha:apps/graph_viewer/requirements.txt" | grep -v '^fastembed==' > "$stage/requirements-graph-lexical.txt"
test -s "$root/apps/graph_viewer/web/dist/index.html"
mkdir -p "$stage/apps/graph_viewer/web"
cp -R "$root/apps/graph_viewer/web/dist" "$stage/apps/graph_viewer/web/"
printf '%s\n' "$sha" > "$stage/REVISION"
# Only the already-verified GitHub Releases job can attach a canonical tag;
# PR/archive validation intentionally cannot produce an activatable HTTP identity.
if [[ "${GITHUB_EVENT_NAME:-}" == release ]]; then
  tag="${GITHUB_REF_NAME:?Base release tag required}"
  [[ "$GITHUB_REF" == "refs/tags/$tag" ]]
  [[ "$tag" =~ ^assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9.-]{1,30})?$ ]]
  [[ "$(git rev-parse --verify "refs/tags/$tag^{commit}")" == "$sha" ]]
  printf '%s\n' "$tag" > "$stage/BASE_RELEASE_TAG"
fi
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
PYTHONPATH="$root/tools/assertion-memory-release" python3 - "$dest/$name" "$sha" "${committed_runtime[@]}" <<'PYQUAL'
from hashlib import file_digest
from pathlib import Path, PurePosixPath
import os
import subprocess
import sys
from tempfile import TemporaryDirectory
from assembly import inspect
from base_payload import SPEC, read_manifest, validate
path,revision=Path(sys.argv[1]),sys.argv[2]
with path.open('rb') as archive:
    digest=file_digest(archive,'sha256').hexdigest()
def qualify(staged):
    key = validate(staged, revision, digest)
    expected = {PurePosixPath(name) for name in sys.argv[3:]}
    preflight = ("synthetic_fixture.py", "smoke_local.py")
    required = {PurePosixPath(name) for name in (
        "REVISION", "QA_PROFILE", "mcp-tool-schema.json", "requirements-graph-lexical.txt", *preflight
    )}
    if (staged / "BASE_RELEASE_TAG").is_file():
        required.add(PurePosixPath("BASE_RELEASE_TAG"))
    prefix = PurePosixPath("apps/graph_viewer/web/dist")
    actual = set(read_manifest(staged))
    dist = {name for name in actual if prefix in name.parents and name.suffix != ".map"}
    if len(expected) != len(sys.argv[3:]) or actual != expected | required | dist:
        raise ValueError("base_runtime_membership")
    for name in expected:
        committed = subprocess.check_output(["git", "show", f"{revision}:{name}"])
        if committed != (staged / name).read_bytes():
            raise ValueError("base_runtime_source_mismatch")
    for helper in preflight:
        committed = subprocess.check_output(["git", "show", f"{revision}:tools/assertion-memory-qa/{helper}"])
        if committed != (staged / helper).read_bytes():
            raise ValueError("base_prod_preflight_source_mismatch")
    # Exercise the root paths extracted from the actual archive as the PROD
    # prepare hook does. No checkout imports, production state or model calls.
    with TemporaryDirectory(prefix="base-prod-preflight-") as directory:
        database = str(Path(directory) / "synthetic-smoke.sqlite3")
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        for helper in preflight:
            subprocess.run(
                [sys.executable, "-B", helper, database],
                cwd=staged, env=environment, check=True, timeout=120,
            )
    return key
inspect(path, digest, spec=SPEC, validate=qualify)
PYQUAL
