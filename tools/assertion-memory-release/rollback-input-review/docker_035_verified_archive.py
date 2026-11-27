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


INDEX_TYPES={"application/vnd.oci.image.index.v1+json",
             "application/vnd.docker.distribution.manifest.list.v2+json"}
MANIFEST_TYPES={"application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.v2+json"}
CONFIG_TYPES={"application/vnd.oci.image.config.v1+json",
              "application/vnd.docker.container.image.v1+json"}
LAYER_TYPES={"application/vnd.oci.image.layer.v1.tar",
             "application/vnd.oci.image.layer.v1.tar+gzip",
             "application/vnd.docker.image.rootfs.diff.tar",
             "application/vnd.docker.image.rootfs.diff.tar.gzip"}


def bind_saved_target(path, descriptor, proof):
    """Bind modern engine Id to reachable hash-verified saved OCI bytes."""
    required=descriptor["digest"]
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<512*1024**2,
            "Invalid Docker save archive")
    with tarfile.open(path,"r:") as t:
        members={}
        for entry in t:
            require(len(members)<64 and entry.name not in members,"Unexpected Docker save member")
            members[entry.name]=entry
        def read(name, max_len=LIMIT):
            m=members.get(name)
            require(m is not None and m.isfile() and 0<m.size<=max_len,"Missing or invalid OCI save member")
            return t.extractfile(m).read()
        root=read("index.json",2*1024**2)
        docs=INDEX_TYPES|MANIFEST_TYPES
        visited=set()
        matches=[]
        def blob(d, types, cap=LIMIT):
            digest=d.get("digest")
            require(type(digest) is str and re.fullmatch(r"sha256:[a-f0-9]{64}",digest)
                    and d.get("mediaType") in types and type(d.get("size")) is int and 0<d["size"]<=cap,
                    "Invalid OCI save descriptor")
            raw=read("blobs/sha256/"+digest[7:], cap)
            require(len(raw)==d["size"] and sha(raw)==digest,"Docker save digest/size mismatch")
            return raw
        def check_platform(d):
            p=d.get("platform")
            require(p is None or (type(p) is dict and p.get("os")=="linux"
                    and p.get("architecture")=="amd64" and not p.get("variant")),
                    "Wrong saved descriptor platform")
        def walk(d,raw,depth,bound=False):
            require(depth<4 and d["digest"] not in visited,"Invalid OCI save descriptor graph")
            visited.add(d["digest"])
            check_platform(d)
            match=d["digest"]==required
            if match:
                require((d["mediaType"],d["size"])==(descriptor["mediaType"],descriptor["size"]),
                        "Inspect target metadata differs from saved bytes")
                matches.append(d["digest"])
            bound=bound or match
            body=json.loads(raw,object_pairs_hook=unique)
            require(body["schemaVersion"]==2 and body.get("mediaType",d["mediaType"])==d["mediaType"],
                    "Wrong saved OCI document kind")
            if d["mediaType"] in INDEX_TYPES:
                refs=body["manifests"]
                # The one-tag, one-platform synthetic export must be unambiguous.
                require(type(refs) is list and len(refs)==1,"Ambiguous saved OCI image")
                child=refs[0]
                walk(child,blob(child,docs,2*1024**2),depth+1,bound)
            else:
                require(bound,"Inspect target is not on the saved runnable graph")
                conf=blob(body["config"],CONFIG_TYPES,2*1024**2)
                layers=body["layers"]
                require(type(layers) is list and len(layers)==3,"Wrong OCI saved layer count")
                ids=verify_payload(conf,[blob(b,LAYER_TYPES) for b in layers])
                require(sha(conf)==proof["id"] and ids==proof["diff_ids"],
                        "Saved target does not bind the exact verified payload")
        r=json.loads(root,object_pairs_hook=unique)
        media=r.get("mediaType","application/vnd.oci.image.index.v1+json")
        require(media in INDEX_TYPES,"Missing Docker save OCI index")
        walk({"digest":sha(root),"size":len(root),"mediaType":media},root,0)
        require(matches==[required],"Expected unique saved target")


def check_inspect(inspect, proof, saved=None):
    """Keep existing exact checks, report only fixed fields and safe digests."""
    errors=[]
    def cleaned(value, pattern):
        return value if type(value) is str and re.fullmatch(pattern,value) else "<missing-or-invalid>"
    digest=r"sha256:[a-f0-9]{64}"
    simple=r"[a-z0-9_-]{1,20}"
    def exact(name, actual, expected, pattern):
        if actual!=expected:
            errors.append(f"{name}: expected {expected}; observed {cleaned(actual,pattern)}")
    require(type(inspect) is dict and type(proof) is dict,"Wrong local inspect metadata shape")
    descriptor=inspect.get("Descriptor")
    rootfs=inspect.get("RootFS")
    rootfs=rootfs if type(rootfs) is dict else {}
    ident=inspect.get("Id")
    target=descriptor if type(descriptor) is dict else {}
    if ident!=proof["id"]:
        if (saved is None or cleaned(ident,digest)!=ident or target.get("digest")!=ident
                or target.get("mediaType") not in INDEX_TYPES|MANIFEST_TYPES
                or type(target.get("size")) is not int):
            exact("Id",ident,proof["id"],digest)
            if target:
                errors.append(f"Descriptor.digest (diagnostic only): {cleaned(target.get('digest'),digest)}")
        else:
            try:
                bind_saved_target(saved,target,proof)
            except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as e:
                # Fixed message, do not expose attacker-controlled tar metadata.
                errors.append(f"Id: expected {proof['id']} or byte-bound local OCI target; "
                              f"observed {ident}; saved target binding failed")
    exact("Os",inspect.get("Os"),"linux",simple)
    exact("Architecture",inspect.get("Architecture"),"amd64",simple)
    layers=rootfs.get("Layers")
    expected=proof["diff_ids"]
    if type(layers) is not list or len(layers)!=len(expected):
        count=len(layers) if type(layers) is list else "<missing-or-invalid>"
        errors.append(f"RootFS.Layers count: expected {len(expected)}; observed {count}")
    if type(layers) is list:
        for pos,(actual,wanted) in enumerate(zip(layers,expected)):
            exact(f"RootFS.Layers[{pos}]",actual,wanted,digest)
    require(not errors,"Docker local image identity mismatch: "+"; ".join(errors))
    return ident


if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    cmd=p.add_subparsers(dest="op",required=True)
    b=cmd.add_parser("write")
    b.add_argument("layout",type=Path)
    b.add_argument("archive",type=Path)
    v=cmd.add_parser("verify")
    v.add_argument("archive",type=Path)
    inspect=cmd.add_parser("check-inspect")
    inspect.add_argument("proof")
    inspect.add_argument("--saved",type=Path)
    a=p.parse_args()
    if a.op=="write":
        write_archive(a.layout,a.archive)
    elif a.op=="verify":
        print(json.dumps(verify_archive(a.archive),sort_keys=True))
    else:
        import sys
        try:
            print(check_inspect(json.load(sys.stdin,object_pairs_hook=unique),json.loads(a.proof),a.saved))
        except (ValueError, KeyError, TypeError) as e:
            # Only emit our controlled comparison errors, never raw inspect JSON.
            if type(e) is ValueError and str(e).startswith("Docker local image identity mismatch:"):
                raise SystemExit(str(e)) from None
            raise SystemExit("Invalid Docker inspect or proof metadata") from None
