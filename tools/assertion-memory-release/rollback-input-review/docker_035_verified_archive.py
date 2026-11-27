"""Derive/validate a local Docker archive from the pinned composed OCI bytes."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

from compose_035_oci_descriptor import COMPOSED, LAYER, encode
from verify_035_oci_root import PIN, unique

LIMIT=192*1024**2
TAG="qualification.invalid/base035:fixed"


def require(test, reason):
    if not test:
        raise ValueError(reason)


def sha(data):
    return "sha256:"+hashlib.sha256(data).hexdigest()


def diffid(raw):
    reader=gzip.GzipFile(fileobj=io.BytesIO(raw)) if raw.startswith(b"\x1f\x8b") else io.BytesIO(raw)
    h=hashlib.sha256()
    size=0
    with reader:
        while chunk:=reader.read(1024*1024):
            size+=len(chunk)
            require(size<=LIMIT,"Oversized decompressed OCI layer")
            h.update(chunk)
    require(size>0,"Empty OCI layer")
    return "sha256:"+h.hexdigest()


def read_blob(root,digest,size):
    require(re.fullmatch(r"sha256:[a-f0-9]{64}",digest) and 0<size<=LIMIT,"Invalid OCI descriptor")
    p=root/digest[7:]
    require(p.is_file() and not p.is_symlink() and p.stat().st_size==size,"Invalid OCI object")
    data=p.read_bytes()
    require(sha(data)==digest,"Wrong pinned OCI blob hash")
    return data


def clayout(layout):
    root=layout/"blobs"/"sha256"
    mid,msz=COMPOSED["manifest"]
    cid,csz=COMPOSED["config"]
    manifest=json.loads(read_blob(root,mid,msz),object_pairs_hook=unique)
    config=read_blob(root,cid,csz)
    info=json.loads(config,object_pairs_hook=unique)
    expected=json.loads(PIN.read_bytes(),object_pairs_hook=unique)["layers"]+[LAYER]
    require([d["digest"] for d in manifest["layers"]]==expected,"Wrong exact OCI manifest layer order")
    require(manifest["config"]["digest"]==cid and manifest["config"]["size"]==csz,"Wrong OCI config")
    layers=[read_blob(root,d["digest"],d["size"]) for d in manifest["layers"]]
    verify_payload(config,layers)
    return config,layers


def verify_payload(config,layers):
    cid,csz=COMPOSED["config"]
    require(len(config)==csz and sha(config)==cid,"Wrong pinned raw image config")
    md=json.loads(config,object_pairs_hook=unique)
    require(md["os"]=="linux" and md["architecture"]=="amd64","Wrong OCI platform")
    ids=[diffid(layer) for layer in layers]
    require(md["rootfs"]=={"type":"layers","diff_ids":ids},"Image decompressed layer DiffIDs differ")
    return ids


def write_archive(layout,archive):
    config,layers=clayout(layout)
    cid=COMPOSED["config"][0][7:]
    config_name=cid+".json"
    layer_names=[f"layer-{i}.tar" for i in range(len(layers))]
    m=[{"Config":config_name,"Layers":layer_names,"RepoTags":[TAG]}]
    def put(t,name,data):
        entry=tarfile.TarInfo(name)
        entry.mode,entry.size=0o644,len(data)
        t.addfile(entry,io.BytesIO(data))
    with tarfile.open(archive,"x:",format=tarfile.USTAR_FORMAT) as t:
        put(t,"manifest.json",encode(m))
        put(t,config_name,config)
        for name,raw in zip(layer_names,layers):
            if raw.startswith(b"\x1f\x8b"):
                raw=gzip.decompress(raw)  # already bounded during verify_payload()
            put(t,name,raw)
    verify_archive(archive)


def verify_archive(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<512*1024**2,
            "Invalid local Docker archive")
    with tarfile.open(path,"r:") as t:
        files={}
        for member in t:
            require(len(files)<64 and member.name not in files,"Unexpected archive members")
            files[member.name]=member
        def read(name):
            require(type(name) is str and re.fullmatch(r"(?:blobs/sha256/)?[A-Za-z0-9_.-]+(?:/layer.tar)?",name),
                    "Invalid archive path")
            member=files[name]
            require(member.isfile() and 0<member.size<=LIMIT,"Invalid archive member")
            return t.extractfile(member).read()
        m=json.loads(read("manifest.json"),object_pairs_hook=unique)
        require(type(m) is list and len(m)==1 and TAG in (m[0].get("RepoTags") or []),
                "Unexpected Docker archive reference")
        layers=m[0]["Layers"]
        require(type(layers) is list and len(layers)==3 and len(set(layers))==3,
                "Unexpected Docker archive layer count")
        ids=verify_payload(read(m[0]["Config"]),[read(name) for name in layers])
        return {"id":COMPOSED["config"][0],"diff_ids":ids,"manifest_representation":"docker-local-archive"}


if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    cmd=p.add_subparsers(dest="op",required=True)
    b=cmd.add_parser("write")
    b.add_argument("layout",type=Path)
    b.add_argument("archive",type=Path)
    v=cmd.add_parser("verify")
    v.add_argument("archive",type=Path)
    a=p.parse_args()
    if a.op=="write":
        write_archive(a.layout,a.archive)
    else:
        print(json.dumps(verify_archive(a.archive),sort_keys=True))
