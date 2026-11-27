"""Compose pinned OCI metadata for the verified native layer; never execute it."""
from hashlib import sha256
import json
from pathlib import Path
import stat

from verify_035_oci_root import PIN, unique, verify, verify_blob

from verify_035_sqlite_provider import NATIVE_LAYER_SHA, NATIVE_LAYER_SIZE

LAYER = "sha256:" + NATIVE_LAYER_SHA
LAYER_SIZE = NATIVE_LAYER_SIZE
IMAGE_CONFIG = "application/vnd.oci.image.config.v1+json"
IMAGE_LAYER = "application/vnd.oci.image.layer.v1.tar"
IMAGE_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
IMAGE_INDEX = "application/vnd.oci.image.index.v1+json"
COMPOSED = {
    "config": ("sha256:f3e1eee55ce80142e1dd9f00251dfe49ebeed80437ce49e3c9077403558fcc32", 4527),
    "manifest": ("sha256:3dafba6ea29fa67f90e74f649383eb2ae3d5d40407f50db0e0ef5a1d7d59a008", 830),
    "index": ("sha256:f1550e5311dcf97e1a1526050654d0137821647c6c294a40dfec5290b45392c6", 289),
}



def descriptor(data, media_type):
    return {"mediaType": media_type, "digest": "sha256:" + sha256(data).hexdigest(), "size": len(data)}


def encode(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def metadata(config, manifest, platform, layer):
    """Return only new config/manifest/index bytes, leaving base objects untouched."""
    if descriptor(layer, IMAGE_LAYER) != {"mediaType": IMAGE_LAYER, "digest": LAYER, "size": LAYER_SIZE}:
        raise ValueError("Wrong pinned SQLite+libffi layer")
    rootfs = config["rootfs"]
    history = config["history"]
    if (rootfs["type"] != "layers" or len(rootfs["diff_ids"]) != len(manifest["layers"])
            or sum(not h.get("empty_layer", False) for h in history) != len(rootfs["diff_ids"])
            or manifest.get("mediaType") != IMAGE_MANIFEST
            or manifest["config"]["mediaType"] != IMAGE_CONFIG):
        raise ValueError("Invalid pinned base root/history")
    config = config | {"rootfs": rootfs | {"diff_ids": rootfs["diff_ids"] + [LAYER]},
                       "history": history + [{"created_by": "assertion-memory Base .35: pinned Ubuntu SQLite + libffi data"}]}
    cb = encode(config)
    cd = descriptor(cb, IMAGE_CONFIG)
    # Do not carry base annotations (they describe a different image).
    mb = encode({"schemaVersion": 2, "mediaType": IMAGE_MANIFEST,
                 "config": cd, "layers": manifest["layers"] + [descriptor(layer, IMAGE_LAYER)]})
    md = descriptor(mb, IMAGE_MANIFEST)
    ib = encode({"schemaVersion": 2, "mediaType": IMAGE_INDEX, "manifests": [md | {"platform": platform}]})
    return {cd["digest"]: cb, md["digest"]: mb}, ib, md


def compose(directory, layer_path, pin):
    verify(directory, pin)
    manifest = verify_blob(directory, pin["manifest"], metadata=True)
    config = verify_blob(directory, pin["config"], metadata=True)
    st = layer_path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size != LAYER_SIZE:
        raise ValueError("Missing fixed native layer bytes")
    layer = layer_path.read_bytes()
    objects, index, manifest_descriptor = metadata(config, manifest, pin["platform"], layer)
    config_descriptor = json.loads(objects[manifest_descriptor["digest"]])["config"]
    for key, desc in (("config", config_descriptor), ("manifest", manifest_descriptor),
                      ("index", descriptor(index, IMAGE_INDEX))):
        if (desc["digest"], desc["size"]) != COMPOSED[key]:
            raise ValueError("Composed " + key + " differs from verified provenance")
    return objects | {LAYER: layer}, index, {
        "base": pin["manifest"], "native_layer": LAYER, "index": descriptor(index, IMAGE_INDEX),
        "manifest": manifest_descriptor, "historical_build_performed": False, "build_approved": False,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_blob_dir", type=Path)
    parser.add_argument("native_layer", type=Path)
    parser.add_argument("new_metadata_dir", type=Path)
    args = parser.parse_args()
    objects, index, report = compose(args.base_blob_dir, args.native_layer,
                                     json.loads(PIN.read_bytes(), object_pairs_hook=unique))
    args.new_metadata_dir.mkdir(mode=0o700, exist_ok=False)
    for key, blob in objects.items():
        (args.new_metadata_dir / key.removeprefix("sha256:")).write_bytes(blob)
    (args.new_metadata_dir / "index.json").write_bytes(index)
    print(json.dumps(report, sort_keys=True))
