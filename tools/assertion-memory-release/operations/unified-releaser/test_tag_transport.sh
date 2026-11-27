#!/usr/bin/env bash
# Local repository + simulated GH JSON; no external access.
set -euo pipefail
src=${1:?path to local reviewed common releaser source}
helper="$(realpath qualify-tag.sh)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
dir=tools/assertion-memory-release/operations/unified-releaser
mkdir -p "$tmp"/{repo,bin,runner}
cd "$tmp/repo"
git init -q
git config user.email fixture@example.invalid
git config user.name Fixture
mkdir -p "$dir"
cp "$src"/{README.md,build_releaser.py,releaser.py,test_releaser.py,workflow.yml.review,package-workflow.yml.review,qualify_release.py,test_qualify_release.py} "$dir/"
git add "$dir"
git commit -qm fixture
export GITHUB_SHA="$(git rev-parse HEAD)"
export RELEASER_COMMIT="$GITHUB_SHA"
export RELEASER_DIGEST="$(cd "$dir" && python3 build_releaser.py)"
export GITHUB_REPOSITORY=Bonzar/craft GITHUB_EVENT_NAME=workflow_dispatch GITHUB_REF=refs/heads/main GITHUB_REF_PROTECTED=true
export RELEASER_LANE=test RELEASER_ENVIRONMENT=assertion-releaser-test
export RELEASER_OPERATION=apply RELEASER_TAG=assertion-releaser-v1.0.0 RELEASER_RELEASE_ID=43
export RUNNER_TEMP="$tmp/runner" PATH="$tmp/bin:$PATH" FIXTURE_REF_COUNT="$tmp/refs"
cat > "$tmp/bin/gh" <<'SH'
#!/bin/bash
case "$2" in
 repos/Bonzar/craft/releases/tags/*) printf '{"tag_name":"%s","id":%s,"immutable":true,"draft":false}\n' "$RELEASER_TAG" "$RELEASER_RELEASE_ID";;
 repos/Bonzar/craft/git/ref/tags/*)
  n=0; [[ ! -f "$FIXTURE_REF_COUNT" ]] || n="$(cat "$FIXTURE_REF_COUNT")"
  printf '%s\n' "$((n+1))" > "$FIXTURE_REF_COUNT"
  sha="$GITHUB_SHA"
  if [[ "${FIXTURE_MOVE:-}" == 1 && "$n" == 1 ]]; then sha=0000000000000000000000000000000000000000; fi
  printf '{"object":{"type":"commit","sha":"%s"}}\n' "$sha";;
 *) exit 9 ;;
esac
SH
chmod +x "$tmp/bin/gh"
bash "$helper"
[[ "$(cat "$FIXTURE_REF_COUNT")" == 2 ]]
rm "$FIXTURE_REF_COUNT"
if FIXTURE_MOVE=1 bash "$helper" >"$tmp/moved.log" 2>&1; then exit 1; fi
[[ "$(cat "$FIXTURE_REF_COUNT")" == 2 ]]
echo 'exact real Git archive rebuild passed; moved second GH ref refused; no public host'
