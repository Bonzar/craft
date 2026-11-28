"""Protected build's explicit opt-in for the packaged Base browser contract."""
from hashlib import sha256
from pathlib import PurePosixPath
import stat

NAME = 'BASE_BROWSER_LISTENER_CONTRACT'
WIRE = b'assertion-memory-base-browser-listener-v1\n'
FILES = (
    'prototypes/assertion_memory/http_server.py',
    'prototypes/assertion_memory/browser_listener.py',
    'prototypes/assertion_memory/browser_surface.py',
    'prototypes/assertion_memory/browser_owner_binding.py',
    'prototypes/assertion_memory/browser_prod_binding.py',
    'prototypes/assertion_memory/base_browser_surface.py',
    'apps/graph_viewer/server/app.py',
    'apps/graph_viewer/server/browser_owner_api.py',
    'apps/graph_viewer/server/existing_owner_store.py',
)


def qualified(path, manifest, *, expected):
    """Verify the actual extracted bytes against its validated SHA256SUMS."""
    member = path / NAME
    if not expected:
        if PurePosixPath(NAME) in manifest or member.exists() or member.is_symlink():
            raise ValueError('base_browser_contract_unexpected')
        return False
    for item in (NAME, *FILES):
        actual = path / item
        mode = actual.lstat().st_mode
        if not stat.S_ISREG(mode) or actual.is_symlink():
            raise ValueError('base_browser_contract_not_regular')
        raw = actual.read_bytes()
        if (not raw or (item == NAME and raw != WIRE)
            or manifest.get(PurePosixPath(item)) != sha256(raw).hexdigest()):
            raise ValueError('base_browser_contract_digest_or_version')
    return True
