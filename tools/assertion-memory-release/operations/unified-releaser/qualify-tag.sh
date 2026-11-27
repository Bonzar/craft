#!/usr/bin/env bash
# Read-only GitHub Actions transport, review-only; never activates a host.
set -euo pipefail
[[ "${GITHUB_REPOSITORY:-}" == Bonzar/craft && "${GITHUB_EVENT_NAME:-}" == workflow_dispatch &&
   "${GITHUB_REF:-}" == refs/heads/main && "${GITHUB_REF_PROTECTED:-}" == true ]]
case "${RELEASER_LANE:-}:${RELEASER_ENVIRONMENT:-}" in
  test:assertion-releaser-test|prod:assertion-releaser-prod) ;;
  *) exit 2 ;;
esac
[[ "$RELEASER_OPERATION" == apply || "$RELEASER_OPERATION" == rollback ]]
[[ "$RELEASER_TAG" =~ ^assertion-releaser-v[0-9]+[.][0-9]+[.][0-9]+$ ]]
[[ "$RELEASER_RELEASE_ID" =~ ^[1-9][0-9]{0,17}$ ]]
[[ "$RELEASER_COMMIT" =~ ^[a-f0-9]{40}$ && "$RELEASER_DIGEST" =~ ^[a-f0-9]{64}$ ]]
[[ "$GITHUB_SHA" =~ ^[a-f0-9]{40}$ && "$(git rev-parse HEAD)" == "$GITHUB_SHA" ]]
git merge-base --is-ancestor "$RELEASER_COMMIT" "$GITHUB_SHA"
dir=tools/assertion-memory-release/operations/unified-releaser
probe() {
  # Release metadata is not a ref witness. Resolve both separately.
  gh api "repos/Bonzar/craft/releases/tags/$RELEASER_TAG" |
    jq -e --arg tag "$RELEASER_TAG" --arg id "$RELEASER_RELEASE_ID" \
    '.tag_name == $tag and (.id|tostring) == $id and .immutable == true and .draft == false' >/dev/null
  local ref
  ref="$(gh api "repos/Bonzar/craft/git/ref/tags/$RELEASER_TAG")"
  local kind sha
  kind="$(jq -er '.object.type' <<<"$ref")"; sha="$(jq -er '.object.sha' <<<"$ref")"
  if [[ "$kind" == tag && "$sha" =~ ^[a-f0-9]{40}$ ]]; then
    ref="$(gh api "repos/Bonzar/craft/git/tags/$sha")"
    [[ "$(jq -er '.tag' <<<"$ref")" == "$RELEASER_TAG" ]]
    kind="$(jq -er '.object.type' <<<"$ref")"; sha="$(jq -er '.object.sha' <<<"$ref")"
  fi
  [[ "$kind" == commit && "$sha" == "$RELEASER_COMMIT" ]]
}
probe
tmp="$(mktemp -d "${RUNNER_TEMP:?}/releaser-tag.XXXXXXXX")"
trap 'rm -rf -- "$tmp"' EXIT
git archive --format=tar "$RELEASER_COMMIT" -- "$dir/" | tar -xf - -C "$tmp"
# No GitHub token travels into the rebuilt source's environment.
(
  cd "$tmp/$dir"
  env -i PATH=/usr/bin:/bin LC_ALL=C python3 -B -m unittest -v test_releaser test_qualify_release
  digest="$(env -i PATH=/usr/bin:/bin LC_ALL=C python3 -B build_releaser.py)"
  [[ "$digest" == "$RELEASER_DIGEST" ]]
  [[ "$(sha256sum assertion-releaser.tar.gz | cut -d ' ' -f1)" == "$RELEASER_DIGEST" ]]
)
probe
printf 'releaser verified %s %s %s; no runtime effect\n' "$RELEASER_TAG" "$RELEASER_COMMIT" "$RELEASER_DIGEST"
