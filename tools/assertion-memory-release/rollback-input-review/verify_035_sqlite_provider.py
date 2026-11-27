"""Verify precisely two signed Ubuntu packages and compose an OCI data layer."""
import datetime as dt
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
import hashlib
import io
import lzma
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

FINGERPRINT = "F6ECB3762474EDA9D21B7022871920D1991BC93C"
INDEX = "main/binary-amd64/Packages.xz"
SNAPSHOT = dt.datetime(2026, 11, 5, tzinfo=dt.timezone.utc)


@dataclass(frozen=True)
class Pin:
    package: str
    suite: str
    version: str
    sha: str
    size: int
    pool: str
    soname: str
    consumer: str
    abi: tuple = ()


SQLITE = Pin("libsqlite3-0", "resolute-updates", "3.46.1-9ubuntu0.3",
             "ff4b5a1e7c028a4bcc30076e4d8197aad1a910df44f4dc8dcf253e9ee9a3a367",
             719538, "pool/main/s/sqlite3", "libsqlite3.so.0", "_sqlite3")
FFI = Pin("libffi8", "resolute", "3.5.2-4",
          "4a7423124bbd459c0aa38b7389ec7802fa6899654767a95fc27de70967fedd48",
          26348, "pool/main/libf/libffi", "libffi.so.8", "_ctypes",
          ("LIBFFI_BASE_8.0", "LIBFFI_CLOSURE_8.0"))
PINS = (FFI, SQLITE)
PYTHON_SHA = "635e1e2c9914bf144c977d6d14d8b6aaee2e3edcd7681d171e314016c73168f7"
PYTHON_SIZE = 95969208
NATIVE_LAYER_SHA = "158c0170fb9c1675be5fc6755e6eadb8a041322207040d1c022a99f19777129b"
NATIVE_LAYER_SIZE = 1597440


def filename(pin):
    return f"{pin.package}_{pin.version}_amd64.deb"


def need(ok, why):
    if not ok:
        raise ValueError(why)


def fields(stanza):
    values = {}
    current = None
    for line in stanza.splitlines():
        if line.startswith((" ", "\t")):
            need(current is not None, "Unexpected continuation")
            values[current] += "\n" + line.strip()
        else:
            current, sep, value = line.partition(":")
            need(sep and re.fullmatch(r"[A-Za-z0-9][-A-Za-z0-9]*", current)
                 and current not in values, "Duplicate or invalid APT field")
            values[current] = value.strip()
    return values


def verify_chain(pin, release, compressed, package):
    metadata = fields(release.strip())
    need(all(metadata.get(k) == v for k, v in {
        "Origin": "Ubuntu", "Codename": "resolute", "Suite": pin.suite,
    }.items()), "Wrong Ubuntu suite")
    date = parsedate_to_datetime(metadata["Date"])
    expiry = metadata.get("Valid-Until")
    need(dt.timedelta(days=7 if pin.suite.endswith("-updates") else 366) >= SNAPSHOT - date >= dt.timedelta()
         and (not expiry or SNAPSHOT < parsedate_to_datetime(expiry)),
         "Release data is not valid at pinned snapshot time")
    rows = [row.split() for row in metadata["SHA256"].splitlines()
            if row.split() and row.split()[-1] == INDEX]
    need(rows == [[hashlib.sha256(compressed).hexdigest(), str(len(compressed)), INDEX]],
         "Invalid signed Packages.xz hash or length")
    with lzma.LZMAFile(io.BytesIO(compressed)) as data:
        index = data.read(50_000_001)
    need(len(index) <= 50_000_000, "Oversized APT index")
    packages = [fields(record) for record in index.decode().strip().split("\n\n")
                if record.startswith(f"Package: {pin.package}\n")]
    matching = [p for p in packages if (p.get("Architecture"), p.get("Version")) ==
                ("amd64", pin.version)]
    require = {"Filename": f"{pin.pool}/{filename(pin)}", "SHA256": pin.sha, "Size": str(pin.size)}
    need(len(matching) == 1 and all(matching[0].get(k) == v for k, v in require.items())
         and len(package) == pin.size and hashlib.sha256(package).hexdigest() == pin.sha, "Wrong pinned Ubuntu package")


def verify(pin, inrelease, keyring, compressed, deb, consumer):
    need(deb.stat().st_size <= 2_000_000 and compressed.stat().st_size <= 10_000_000,
         "Oversized review input")
    with tempfile.TemporaryDirectory() as t:
        home = Path(t)
        signed = subprocess.run(["/usr/bin/gpgv", "--homedir", str(home),
                                 "--keyring", str(keyring.resolve()), "--status-fd", "1",
                                 "--output", str(home / "Release"), str(inrelease)],
                                capture_output=True, check=True, timeout=20)
        verified = [row.split() for row in signed.stdout.decode("ascii").splitlines()
                    if row.startswith("[GNUPG:] VALIDSIG ")]
        need(any(row[2] == FINGERPRINT or row[-1] == FINGERPRINT for row in verified),
             "Wrong Ubuntu archive signing key")
        verify_chain(pin, (home / "Release").read_text(), compressed.read_bytes(), deb.read_bytes())
        payload = subprocess.run(["/usr/bin/dpkg-deb", "--fsys-tarfile", str(deb)],
                                 capture_output=True, check=True, timeout=20).stdout
        prefix = "./usr/lib/x86_64-linux-gnu/"
        with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
            link = tar.getmember(prefix + pin.soname)
            need(link.issym() and relative_target(pin, link.linkname),
                 "Unexpected package SONAME symlink")
            real = tar.getmember(prefix + link.linkname)
            need(real.isfile() and real.size <= 4_000_000, "Unexpected SQLite ELF member")
            library = home / "provider.so"
            library.write_bytes(tar.extractfile(real).read())
        for path, expression in ((consumer, rf"\(NEEDED\).*\[{re.escape(pin.soname)}\]"),
                                 (library, rf"\(SONAME\).*\[{re.escape(pin.soname)}\]")):
            elf = subprocess.run(["/usr/bin/readelf", "-h", "-d", "-V", "--", str(path)],
                                 capture_output=True, text=True, check=True, timeout=10,
                                 env={"LC_ALL": "C"}).stdout
            need(re.search(r"Class:\s+ELF64", elf) and
                 re.search(r"Machine:\s+Advanced Micro Devices X86-64", elf)
                 and re.search(expression, elf), "ELF architecture or SONAME mismatch")
            if pin.abi:
                term = "Version needs section" if path == consumer else "Version definition section"
                need(term in elf, "Missing ELF version section")
                section = elf.split(term, 1)[1].split("\nVersion ", 1)[0]
                if path == consumer:
                    parts = re.split(r"\bFile: ", section)[1:]
                    section = next((part for part in parts if part.split()[0] == pin.soname), "")
                need(all(re.search(r"\bName: " + re.escape(v) + r"(?=\s|$)", section)
                         for v in pin.abi), "Mismatched libffi ABI")
        return link.linkname, library.read_bytes()


def relative_target(pin, name):
    return re.fullmatch(re.escape(pin.soname) + r"\.[0-9]+(?:\.[0-9]+)*", name)


def make_layer(providers):
    """Ordering and membership are fixed: two SONAME/file pairs, never scripts."""
    path = "usr/lib/x86_64-linux-gnu/"
    need(set(providers) == {pin.package for pin in PINS}, "Wrong provider set")
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w", format=tarfile.USTAR_FORMAT) as layer:
        for pin in PINS:
            name, data = providers[pin.package]
            need(relative_target(pin, name) and 0 < len(data) <= 4_000_000,
                 "Invalid verified provider")
            entry = tarfile.TarInfo(path + name)
            entry.mode, entry.size = 0o644, len(data)
            layer.addfile(entry, io.BytesIO(data))
            link = tarfile.TarInfo(path + pin.soname)
            link.mode, link.type, link.linkname = 0o777, tarfile.SYMTYPE, name
            layer.addfile(link)
    return result.getvalue()


def regular(path, limit):
    import stat
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit, "Invalid staged input")
    return path


def consumers(archive, destination):
    with regular(archive, PYTHON_SIZE).open("rb") as f:
        need(archive.stat().st_size == PYTHON_SIZE and
             hashlib.file_digest(f, "sha256").hexdigest() == PYTHON_SHA,
             "Wrong pinned CPython bytes")
        f.seek(0)
        wanted = {f"{pin.consumer}.cpython-312-x86_64-linux-gnu.so": pin.consumer for pin in PINS}
        found = {}
        with tarfile.open(fileobj=f, mode="r:*") as t:
            for m in t:
                base = m.name.rsplit("/", 1)[-1]
                if base not in wanted:
                    continue
                key = wanted[base]
                need(m.isfile() and key not in found and 0 < m.size <= 4_000_000,
                     "Invalid CPython ELF consumer")
                dest = destination / base
                dest.write_bytes(t.extractfile(m).read())
                found[key] = dest
    need(set(found) == set(wanted.values()), "Missing pinned CPython ELF consumer")
    return found


def compose(root, *, output=None):
    keyring = regular(root / "ubuntu-archive-keyring.gpg", 1_000_000)
    with tempfile.TemporaryDirectory() as t:
        cp = consumers(root / "cpython.tar.gz", Path(t))
        inputs = {}
        for pin in PINS:
            base = root / pin.suite
            inputs[pin.package] = verify(pin, regular(base / "InRelease", 2_000_000),
                keyring, regular(base / "Packages.xz", 10_000_000),
                regular(base / filename(pin), pin.size), cp[pin.consumer])
        layer = make_layer(inputs)
        need(len(layer) == NATIVE_LAYER_SIZE and hashlib.sha256(layer).hexdigest() == NATIVE_LAYER_SHA,
             "Composed native layer differs from independently verified pin")
    if output is not None:
        with output.open("xb") as target:
            target.write(layer)
    else:
        staged = regular(root / "native-library-layer.tar", len(layer))
        need(staged.read_bytes() == layer, "Staged native library layer differs from signed material")
    return {"schema": "base-035-linux-libraries-review-layer/v2",
            "packages": [{"package": p.package, "suite": p.suite, "version": p.version,
                          "sha256": p.sha, "size": p.size} for p in PINS],
            "layer_diff_id": "sha256:" + hashlib.sha256(layer).hexdigest(),
            "layer_size": len(layer), "build_approved": False}


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", required=True, type=Path)
    parser.add_argument("--layer", type=Path)
    args = parser.parse_args()
    print(json.dumps(compose(args.compose, output=args.layer), sort_keys=True))
