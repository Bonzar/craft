"""Review-only one-replica qualifier: no publish, deploy or signing."""
import json
import os
from pathlib import Path
import subprocess
import sys

import compare_035_full_payload as c
import package_035_rebuild as q
from rollback_rebuild_origin import Policy


def git(*args):
    return subprocess.run(("git",*args),check=True,timeout=30,capture_output=True).stdout


def list_paths(revision,roots):
    if revision!=q.REVISION:
        raise ValueError("Wrong Git selection")
    tree=git("ls-tree","-rz","--full-tree",revision,"--",*roots)
    if len(tree)>2*1024**2:
        raise ValueError("Historical Git tree too large")
    for row in tree.rstrip(b"\0").split(b"\0"):
        meta,path=row.split(b"\t",1)
        mode,kind,_=meta.split()
        if mode not in (b"100644",b"100755") or kind!=b"blob":
            raise ValueError("Unexpected Git source shape")
        yield path.decode("ascii")


def read_blob(revision,path):
    if revision!=q.REVISION:
        raise ValueError("Wrong Git selection")
    pointer=revision+":"+path
    size=int(git("cat-file","-s",pointer))
    if not 0<size<=32*1024**2:
        raise ValueError("Historical Git blob too large")
    data=git("cat-file","blob",pointer)
    if len(data)!=size:
        raise ValueError("Historical blob changed")
    return data


def qualify(proof,env):
    contexts={"GITHUB_REPOSITORY":"Bonzar/craft","GITHUB_REF":"refs/heads/main",
              "GITHUB_REF_PROTECTED":"true","GITHUB_EVENT_NAME":"workflow_dispatch",
              "GITHUB_RUN_ATTEMPT":"1"}
    if (any(env.get(k)!=v for k,v in contexts.items()) or
        git("rev-parse","HEAD").decode().strip()!=env.get("GITHUB_SHA") or
        git("rev-parse",f"refs/tags/{c.TAG}^{{commit}}").decode().strip()!=q.REVISION):
        raise ValueError("Expected fixed first-attempt protected source")
    # This is intentionally an unsigned local policy, never a protected
    # workflow ID or promotion credential. Nothing here authorizes sealing.
    policy=Policy(1,env["GITHUB_SHA"],c.TAG,q.REVISION,c.RELEASE_ID,"base","base-v1")
    return c.qualify_full(proof,policy,list_paths,read_blob)


if __name__=="__main__":
    result=qualify(Path(sys.argv[1]),os.environ)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as out:
        # No names, per-file manifest rows, or data bytes leave the runner.
        for key in ("release_id","revision","verified_graph_catalogue_tar_sha256",
                    "full_base_archive_sha256","full_file_manifest_sha256","full_member_count"):
            out.write(f"{key}={result[key]}\n")
