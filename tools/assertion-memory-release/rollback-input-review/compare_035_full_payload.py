"""Review bridge: two independently built outputs into canonical Base-v1 packer."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tarfile
from tempfile import TemporaryDirectory

import package_035_rebuild as qa
from rollback_rebuild_base_compile import assemble
from rollback_rebuild_origin import Policy

TYPE="base-035-rebuild-artifact-graph/v1"
TAG="assertion-memory-v0.1.35"
RELEASE_ID="459435759"

def file_digest(path):
    from hashlib import file_digest as digest
    with path.open("rb") as f:
        return digest(f,"sha256").hexdigest()


def _policy(policy):
    if (type(policy) is not Policy or
        (policy.tag,str(policy.release_id),policy.revision,policy.profile,policy.layout)
        !=(TAG,RELEASE_ID,qa.REVISION,"base","base-v1")):
        raise ValueError("Wrong fixed historical Base selection")



def _assemble_one(proof,sandbox,policy,list_paths,read_source,outputs):
    sandbox.mkdir(mode=0o700)
    with tarfile.open(proof/qa.ARCHIVE,"r:") as t:
        for m in t:
            if not m.isfile() or m.name not in outputs["files"]:
                raise ValueError("Unexpected rebuilt output")
            out=sandbox/m.name
            out.parent.mkdir(parents=True,exist_ok=True)
            with t.extractfile(m) as source,out.open("xb") as dest:
                shutil.copyfileobj(source,dest)
            if file_digest(out)!=outputs["files"][m.name]:
                raise ValueError("Rebuilt output changed during trusted staging")
    catalogue=json.loads((sandbox/qa.CATALOG).read_bytes())
    if (type(catalogue) is not dict or catalogue.get("version")!=2 or
        catalogue.get("revision")!=qa.REVISION or
        type(catalogue.get("tools")) is not dict or not catalogue["tools"]):
        raise ValueError("Invalid exact historical MCP catalogue identity")
    archive=sandbox/"base-cold.tar.gz"
    digest=assemble(policy,sandbox/"dist",sandbox/qa.CATALOG,archive,list_paths,read_source)
    with tarfile.open(archive,"r:gz") as t:
        rows=t.getmember("SHA256SUMS")
        if not rows.isfile() or not 0<rows.size<=4*1024**2:
            raise ValueError("Invalid full payload SHA256SUMS")
        with t.extractfile(rows) as f:
            manifest=f.read()
    return archive,digest,manifest


def _receipt(item,outputs):
    return {"schema":TYPE,"release_id":RELEASE_ID,"revision":qa.REVISION,
            "verified_graph_catalogue_tar_sha256":outputs["sha256"],
            "full_base_archive_sha256":item[1],
            "full_file_manifest_sha256":sha256(item[2]).hexdigest(),
            "full_member_count":len(item[2].splitlines()),
            "artifact_status":"unsigned_comparison_only","archive_published":False}


def qualify_full(proof,policy,list_paths,read_source):
    _policy(policy)
    outputs=qa.compare(proof,proof)  # verify one local receipt against its actual tar
    with TemporaryDirectory() as d:
        full=_assemble_one(proof,Path(d)/"replica",policy,list_paths,read_source,outputs)
        return _receipt(full,outputs)


def compare_full(a,b,policy,list_paths,read_source):
    _policy(policy)
    outputs=qa.compare(a,b)
    with TemporaryDirectory() as d:
        root=Path(d)
        full=[_assemble_one(proof,root/side,policy,list_paths,read_source,outputs)
              for side,proof in (("a",a),("b",b))]
        first,second=full
        if first[1:]!=second[1:]:
            raise ValueError("Full Base manifest or archive digest diverged")
        with first[0].open("rb") as one,second[0].open("rb") as two:
            while block:=one.read(1024*1024):
                if block!=two.read(1024*1024):
                    raise ValueError("Full Base compressed bytes diverged")
            if two.read(1):
                raise ValueError("Full Base compressed length diverged")
        return _receipt(first,outputs)
