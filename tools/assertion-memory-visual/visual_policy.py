"""Product-only payload validation for the common release receiver.

Never extracts, deploys, restarts, swaps pointers or reads secrets.  The common
transaction may call validate() on extracted staging or a previously retained
candidate before choosing its immutable release key.
"""
import hashlib
import json
from pathlib import Path
import re

MAX_ARCHIVE_BYTES = 5_000_000
MAX_MEMBER_BYTES = 2_400_000
MAX_MEMBERS = 5
ITEMS = [r'index\.html',r'assets/index-[A-Za-z0-9_-]+\.js',r'assets/index-[A-Za-z0-9_-]+\.css',
         r'assets/graph\.worker-[A-Za-z0-9_-]+\.js']
HEX = re.compile('[a-f0-9]{64}')


def validate(payload: Path, *, allow_prepared=False) -> str:
    """Return stable browser ID, or fail closed when any file/release is invalid."""
    root = Path(payload)
    marker = root/'manifest.json'
    if (root.is_symlink() or not root.is_dir() or (root/'assets').is_symlink()
            or not (root/'assets').is_dir() or marker.is_symlink()
            or not marker.is_file() or marker.stat().st_size > 1024):
        raise ValueError('invalid_visual_layout')
    raw = marker.read_bytes()
    doc = json.loads(raw)
    if (not isinstance(doc, dict) or len(doc) != 4
            or any(sum(bool(re.fullmatch(rule, key)) for key in doc) != 1 for rule in ITEMS)
            or any(not isinstance(value,str) or not HEX.fullmatch(value) for value in doc.values())
            or raw != (json.dumps(doc, sort_keys=True, separators=(',', ':'))+'\n').encode()):
        raise ValueError('invalid_visual_manifest')
    entries = set()
    for item in root.rglob('*'):
        name = item.relative_to(root).as_posix()
        if item.is_symlink() or (name != 'assets' and not item.is_file()):
            raise ValueError('invalid_visual_member')
        if name == '.prepared-v1' and allow_prepared:
            continue  # Trusted core owns marker validation; never a TAR member.
        if name != 'assets': entries.add(name)
    if entries != {*doc, 'manifest.json'}:
        raise ValueError('visual_manifest_membership')
    for name, digest in doc.items():
        p = root/name
        if p.stat().st_size > MAX_MEMBER_BYTES:
            raise ValueError('visual_member_size')
        with p.open('rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest() != digest:
                raise ValueError('visual_manifest_digest')
    return hashlib.sha256(raw).hexdigest()


def accept_member(entry, name):
    """Additional product rule called by the common TAR reader, after path safety."""
    path = name.as_posix()
    if path == 'manifest.json':
        return entry.isfile() and 0 < entry.size <= 1024
    return entry.isfile() and 0 < entry.size <= MAX_MEMBER_BYTES and any(
        re.fullmatch(rule,path) for rule in ITEMS)


def retained(incoming, final):
    if validate(incoming) != validate(final, allow_prepared=True):
        raise ValueError('visual_retained_digest')


def make_hook(root):
    """Visual never restarts a service; test the in-scope atomic pointer after a swap."""
    root = Path(root).absolute()

    def hook(action, release):
        if action in ('prepare', 'restart'):
            return
        if action == 'stop':
            marker = root/'current'
            if marker.exists() or marker.is_symlink():
                raise ValueError('visual_pointer_still_present')
            return
        if action not in ('smoke', 'restore'):
            raise ValueError('visual_hook_action')
        release = Path(release)
        if release.absolute().parent != root/'releases':
            raise ValueError('visual_hook_scope')
        if release.name != validate(release,allow_prepared=True):
            raise ValueError('visual_release_identity')
        marker = root/'current'
        target = Path(marker.readlink())
        if target not in (release.absolute(), Path('releases')/release.name):
            raise ValueError('visual_wrong_active_release')

    return hook


def archive_spec():
    import release_core
    return release_core.ArchiveSpec(tar_mode='r|', max_archive_bytes=MAX_ARCHIVE_BYTES,
        max_expanded_bytes=MAX_ARCHIVE_BYTES, max_members=MAX_MEMBERS,
        accept_member=accept_member, allow_dirs=False)


def deploy_verified(stream, expected_sha, *, root):
    """Apply the product adapter after caller verifies its environment/signature authority."""
    import release_core
    root=Path(root)
    hook=make_hook(root)
    def locked(_fd):
        import os
        if root.name=='test' and os.path.lexists(root/'runtime'/'.visual-runtime-joint-transition'):
            raise ValueError('Unsettled TEST Visual joint transition')
        return hook
    return release_core.deploy(stream, expected_sha, root=root, spec=archive_spec(),
        validate=validate, hook=hook, retained=retained, hook_factory=locked)
