"""Normalize and independently compare the two historical build payloads."""
import hashlib
import json
from pathlib import Path
import re
import stat
import tarfile

LIMIT=192*1024**2
REVISION="710cf557d29cf91e6ab1550d27e525817df96588"
CATALOG="mcp-tool-schema.json"
ARCHIVE="payload.tar"
WITNESS="content-witness.json"


def need(test, why):
    if not test:
        raise ValueError(why)


def valid_name(name):
    return name==CATALOG or (name.startswith("dist/") and len(name)<=100 and
          all(p not in {".",".."} and re.fullmatch(r"[a-zA-Z0-9_.@+-]+",p) for p in name.split("/")))


def shell_pack(dist, catalog, proof):
    need(stat.S_ISDIR(dist.lstat().st_mode) and not proof.exists(), "Invalid build directories")
    candidates={CATALOG:catalog}
    for p in dist.rglob("*"):
        st=p.lstat()
        if stat.S_ISDIR(st.st_mode):
            continue
        candidates["dist/"+p.relative_to(dist).as_posix()]=p
    need("dist/index.html" in candidates and len(candidates)<=1000,"Incomplete Graph payload")
    proof.mkdir(mode=0o700)
    with tarfile.open(proof/ARCHIVE,"x:",format=tarfile.USTAR_FORMAT) as t:
        for name,p in sorted(candidates.items()):
            st=p.lstat()
            need(valid_name(name) and stat.S_ISREG(st.st_mode) and 0<st.st_size<=LIMIT,
                 "Unexpected historical build output")
            member=tarfile.TarInfo(name)
            member.mode,member.size=0o644,st.st_size
            with p.open("rb") as f:
                t.addfile(member,f)
    result=inspect(proof/ARCHIVE)
    (proof/WITNESS).write_text(json.dumps(result,sort_keys=True,separators=(",",":"))+"\n")


def inspect(archive):
    n=archive.lstat()
    need(stat.S_ISREG(n.st_mode) and 0<n.st_size<=LIMIT,"Unexpected output archive")
    with archive.open("rb") as src:
        payload=hashlib.file_digest(src,"sha256").hexdigest()
    items={}
    with tarfile.open(archive,"r:") as t:
        previous=""
        for m in t:
            need(len(items)<1000 and valid_name(m.name) and m.name>previous and m.isfile()
                 and m.mode==0o644 and m.mtime==m.uid==m.gid==0 and not m.pax_headers
                 and not m.uname and not m.gname and 0<m.size<=LIMIT,
                 "Noncanonical historical payload")
            previous=m.name
            with t.extractfile(m) as f:
                items[m.name]=hashlib.file_digest(f,"sha256").hexdigest()
    need(CATALOG in items and "dist/index.html" in items,"Missing historical payload")
    return {"revision":REVISION,"sha256":payload,"files":items,"status":"unsigned_build_only"}


def compare(a,b):
    proofs=[]
    for folder in (a,b):
        need({p.name for p in folder.iterdir()}=={ARCHIVE,WITNESS},"Unexpected proof files")
        summary=inspect(folder/ARCHIVE)
        record=folder/WITNESS
        st=record.lstat()
        need(stat.S_ISREG(st.st_mode) and 0<st.st_size<256*1024 and
             json.loads(record.read_text())==summary,"Unpacked witness does not match payload")
        proofs.append(summary)
    need(proofs[0]==proofs[1],"Independent build outputs diverged")
    with (a/ARCHIVE).open("rb") as x,(b/ARCHIVE).open("rb") as y:
        while part:=x.read(1024**2):
            need(part==y.read(1024**2),"Independent packed output bytes differ")
        need(y.read(1)==b"","Independent packed output sizes differ")
    return proofs[0]


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_subparsers(dest="op",required=True)
    for cmd, args in (("pack",("dist","catalog","proof")),("compare",("a","b"))):
        act=actions.add_parser(cmd)
        for name in args: act.add_argument(name,type=Path)
    a=parser.parse_args()
    if a.op=="pack": shell_pack(a.dist,a.catalog,a.proof)
    else: print(json.dumps(compare(a.a,a.b),sort_keys=True))
