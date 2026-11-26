"""Inactive verifier for protected-main reconstruction of an old Base payload."""
from dataclasses import dataclass
import re

SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TAG = re.compile(r"assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+")
ROOT = "/repos/Bonzar/craft"
REPOSITORY_ID = 1260097465
WORKFLOW = ".github/workflows/base-reconstruct.yml"
MAIN = "refs/heads/main"


class Reject(ValueError):
    pass


def need(condition, reason):
    if not condition:
        raise Reject(reason)


def exact_ancestor(get, sha):
    need(type(sha) is str and SHA.fullmatch(sha), "Full source SHA required")
    commit = get(f"{ROOT}/commits/{sha}")
    compare = get(f"{ROOT}/compare/{sha}...main")
    need(commit.get("sha") == sha and compare.get("status") in ("ahead", "identical")
         and compare.get("merge_base_commit", {}).get("sha") == sha,
         "Commit outside main lineage")
    return sha


def verify_source_selection(get, *, control_sha, payload_sha, tag):
    main = get(f"{ROOT}/branches/main")
    need(main.get("protected") is True and main.get("commit", {}).get("sha") == control_sha,
         "Reconstruction must initiate on current protected main")
    need(type(tag) is str and TAG.fullmatch(tag), "New canonical Base version required")
    exact_ancestor(get, control_sha)
    return exact_ancestor(get, payload_sha)


@dataclass(frozen=True)
class Proof:
    tag: str
    release_id: int
    control_sha: str
    payload_sha: str
    run_id: int
    archive_digest: str


def verify_published(get, proof, *, workflow_id, verify_archive_attestation):
    """Re-check completed first-run provenance. Caller must verify actual archive attestation.

    verify_archive_attestation is a mandatory independent GitHub artifact
    attestation (or equivalent pinned SLSA) verifier, not manifest self-claims.
    """
    main = get(f"{ROOT}/branches/main")
    need(main.get("protected") is True, "Main protection missing")
    need(type(proof.tag) is str and TAG.fullmatch(proof.tag), "Not a canonical Base tag")
    need(all(type(x) is int and x > 0 for x in
             (proof.release_id, proof.run_id, workflow_id)), "Invalid GitHub identity")
    need(type(proof.archive_digest) is str and DIGEST.fullmatch(proof.archive_digest),
         "Invalid archive digest")
    exact_ancestor(get, proof.control_sha)
    exact_ancestor(get, proof.payload_sha)
    run = get(f"{ROOT}/actions/runs/{proof.run_id}")
    need((run.get("id"), run.get("workflow_id"), run.get("path"), run.get("head_sha"),
          run.get("head_branch"), run.get("event"), run.get("status"),
          run.get("conclusion"), run.get("run_attempt")) ==
         (proof.run_id, workflow_id, WORKFLOW, proof.control_sha, "main",
          "workflow_dispatch", "completed", "success", 1)
         and run.get("head_repository", {}).get("id") == REPOSITORY_ID
         and run.get("repository", {}).get("id") == REPOSITORY_ID,
         "Wrong reconstruction producer")
    release = get(f"{ROOT}/releases/tags/{proof.tag}")
    need((release.get("id"), release.get("tag_name"), release.get("draft"),
          release.get("immutable")) == (proof.release_id, proof.tag, False, True),
         "Wrong immutable release")
    ref = get(f"{ROOT}/git/ref/tags/{proof.tag}")
    need(ref.get("ref") == "refs/tags/" + proof.tag, "Wrong release ref")
    target = ref.get("object", {})
    if target.get("type") == "tag":
        tag_object_sha = target.get("sha")
        need(type(tag_object_sha) is str and SHA.fullmatch(tag_object_sha), "Invalid tag object")
        tagged = get(f"{ROOT}/git/tags/{tag_object_sha}")
        need(tagged.get("sha") == tag_object_sha and tagged.get("tag") == proof.tag,
             "Wrong annotated tag")
        target = tagged.get("object", {})
    need(target.get("type") == "commit" and target.get("sha") == proof.payload_sha,
         "Tag selects different payload")
    assets = [x for x in release.get("assets", [])
              if x.get("name") == "assertion-memory-base-reconstructed.tar.gz"]
    need(len(assets) == 1 and assets[0].get("state") == "uploaded"
         and type(assets[0].get("id")) is int and assets[0]["id"] > 0
         and assets[0].get("digest") == proof.archive_digest,
         "Exact immutable release asset missing")
    need(verify_archive_attestation(proof, workflow_id, assets[0]["id"]) is True,
         "Archive build attestation failed")
    return assets[0]["id"]
