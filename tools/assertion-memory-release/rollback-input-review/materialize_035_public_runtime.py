"""Public-only fixed .35 OCI/runtime acquisition; no engine or package execution."""
import hashlib
import json
from pathlib import Path
import shutil
from urllib.parse import urlencode, urlsplit
from urllib.request import (build_opener, Request, HTTPRedirectHandler)

from verify_035_oci_root import PIN, verify
from verify_035_sqlite_provider import PINS, filename, PYTHON_SHA, PYTHON_SIZE, compose
from stage_035_oci_layout import COMPOSED

NODE_SHA = "4f2b175a6d53a5c617a80dba2676238a0688cef28c6da4eba35c7d844e66fcfb"
NATIVE_LAYER = "native-library-layer.tar"
SNAPSHOT = "https://snapshot.ubuntu.com/ubuntu/20261105T000000Z/"
REGISTRY = "registry-1.docker.io"
OCI_ACCEPT = ", ".join("application/vnd.oci.image." + x for x in
                       ("index.v1+json", "manifest.v1+json"))
CDN = {REGISTRY, "production.cloudflare.docker.com",
       "docker-images-prod.6aa30f8b08e16409b46e0173d6de2f56.r2.cloudflarestorage.com"}
GH_CDN = {"api.github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}


def check_url(url, allowed):
    u = urlsplit(url)
    if u.scheme != "https" or u.netloc not in allowed or u.fragment:
        raise ValueError("Unapproved public ingress origin")


class PublicRedirect(HTTPRedirectHandler):
    def __init__(self, allowed):
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.allowed)
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        # Never forward registry auth even if a redirected destination is on
        # the public allowlist. Each primary object starts at a fresh URL.
        for key in ("Authorization", "Cookie", "Proxy-Authorization"):
            result.remove_header(key)
        return result


def fetch(url, allowed, output=None, *, limit, digest=None, size=None, headers=None):
    check_url(url, allowed)
    h = hashlib.sha256()
    count = 0
    opener = build_opener(PublicRedirect(allowed))
    with opener.open(Request(url, headers=headers or {}), timeout=45) as response:
        if output is None:
            blob = response.read(limit+1)
            if len(blob) > limit:
                raise ValueError("Oversized public metadata")
            return blob
        partial = output.with_suffix(output.suffix+".partial")
        try:
            with partial.open("xb") as dest:
                while chunk := response.read(65536):
                    count += len(chunk)
                    if count > limit:
                        raise ValueError("Oversized public input")
                    h.update(chunk)
                    dest.write(chunk)
            if not count or size is not None and count != size or digest and h.hexdigest() != digest:
                raise ValueError("Public material digest or size mismatch")
            if output.exists() or output.is_symlink():
                raise ValueError("Public material already exists")
            partial.rename(output)
        finally:
            partial.unlink(missing_ok=True)


def materialize(root, *, get=fetch, stage_keyring):
    pin = json.loads(PIN.read_bytes())
    oci = root/"oci-blobs"
    oci.mkdir()
    token_host = "auth.docker.io"
    query = urlencode({"service":"registry.docker.io","scope":"repository:library/ubuntu:pull"})
    token = json.loads(get("https://"+token_host+"/token?"+query,{token_host},limit=128000))["token"]
    if not isinstance(token,str) or len(token)>32000 or any(c.isspace() for c in token):
        raise ValueError("Invalid public registry token")
    url = "https://"+REGISTRY+"/v2/library/ubuntu/"
    for key in ("index","manifest","config","layers"):
        for digest in pin[key] if key == "layers" else [pin[key]]:
            part = "manifests" if key in {"index","manifest"} else "blobs"
            get(url+part+"/"+digest,CDN,oci/digest[7:],limit=128*1024**2,digest=digest[7:],
                headers={"Accept":OCI_ACCEPT,"Authorization":"Bearer "+token})
    verify(oci,pin)
    native = root/"native-libraries"
    native.mkdir()
    # Callback receives only verified base OCI bytes, never an installed host keyring.
    stage_keyring(oci, pin, native/"ubuntu-archive-keyring.gpg")
    get("https://api.github.com/repos/actions/python-versions/releases/assets/676818394",
        GH_CDN,native/"cpython.tar.gz",limit=PYTHON_SIZE,digest=PYTHON_SHA,size=PYTHON_SIZE,
        headers={"Accept":"application/octet-stream"})
    get("https://nodejs.org/dist/v24.22.3/node-v24.22.3-linux-x64.tar.xz",
        {"nodejs.org"},root/"node.tar.xz",limit=96*1024**2,digest=NODE_SHA)
    for p in PINS:
        suite = native/p.suite
        suite.mkdir()
        distro = SNAPSHOT+"dists/"+p.suite+"/"
        for remote, local, cap in (("InRelease","InRelease",2_000_000),
                                    ("main/binary-amd64/Packages.xz","Packages.xz",10_000_000)):
            get(distro+remote,{"snapshot.ubuntu.com"},suite/local,limit=cap)
        get(SNAPSHOT+p.pool+"/"+filename(p),{"snapshot.ubuntu.com"},suite/filename(p),
            limit=p.size,digest=p.sha,size=p.size)
    return compose(native,output=native/NATIVE_LAYER)


def from_verified_oci(oci, pin, output):
    """Read the pinned single-path keyring without unpacking an image."""
    import tarfile
    verify(oci, pin)
    key = "usr/share/keyrings/ubuntu-archive-keyring.gpg"
    candidates = []
    for digest in pin["layers"]:
        with tarfile.open(oci/digest[7:], "r|*") as layer:
            for member in layer:
                name = member.name.removeprefix("./")
                if name == key:
                    if not member.isfile() or not 0 < member.size <= 1024*1024:
                        raise ValueError("Unexpected archive keyring member")
                    candidates.append(layer.extractfile(member).read())
                if name == "usr/share/keyrings/.wh.ubuntu-archive-keyring.gpg":
                    raise ValueError("OCI archive keyring is whiteouted")
    if len(candidates) != 1:
        raise ValueError("No unique pinned OCI archive keyring")
    with output.open("xb") as f:
        f.write(candidates[0])


if __name__ == "__main__":
    import sys
    report = materialize(Path(sys.argv[1]), stage_keyring=from_verified_oci)
    print(json.dumps(report, sort_keys=True))
