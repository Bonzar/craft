"""Code-only local release channels. Never opens a database or controls processes.

The caller verifies the checkout/archive and supplies its source commit. Future
request processes can import from CHANNEL/current; already running workers keep
using their loaded code and require a separate, precisely owned restart.
"""
from contextlib import contextmanager
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid


_SHA = re.compile(r'[0-9a-f]{40}\Z')
_SKIP = {'tests', '__pycache__'}


def _commit(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError('A full lowercase 40-character source commit is required')
    return value


def _code_files(root):
    """Return a deterministic allowlist, rejecting symlinks before reading bytes."""
    result = {}
    package = root / 'prototypes' / 'assertion_memory'
    for ancestor in (root, root / 'prototypes', package):
        if ancestor.is_symlink() or not ancestor.is_dir():
            raise ValueError('Source package must contain real directories')
    for directory, names, files in os.walk(package, followlinks=False):
        parent = Path(directory)
        names[:] = sorted(n for n in names if n not in _SKIP and not n.startswith('.'))
        if any((parent / n).is_symlink() for n in names):
            raise ValueError('Source directory symlinks are not allowed')
        for name in sorted(files):
            path = parent / name
            if path.is_symlink():
                raise ValueError('Source file symlinks are not allowed')
            allowed = name.endswith('.py') and not name.endswith('_test.py')
            allowed = allowed or (name.startswith('requirements') and name.endswith('.txt'))
            if allowed and not name.startswith('.'):
                result[path.relative_to(root).as_posix()] = path
    initializer = root / 'prototypes' / '__init__.py'
    if initializer.is_symlink():
        raise ValueError('Source package symlinks are not allowed')
    if initializer.is_file():
        result['prototypes/__init__.py'] = initializer
    if 'prototypes/assertion_memory/__init__.py' not in result:
        raise ValueError('Source package initializer is missing')
    return result


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_write(path, value):
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _channel(path):
    channel = Path(path).absolute()
    if channel.is_symlink():
        raise ValueError('Channel must be a real directory')
    channel.mkdir(parents=True, exist_ok=True)
    releases = channel / 'releases'
    if releases.is_symlink():
        raise ValueError('Release directory cannot be a symlink')
    releases.mkdir(exist_ok=True)
    return channel


@contextmanager
def _locked(channel):
    lock = channel / '.release.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def validate_release(path):
    """Verify every file, reject extra files and all symlinks before promotion."""
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Release must be a real directory')
    actual = set()
    for directory, names, files in os.walk(path, followlinks=False):
        for name in names + files:
            if (Path(directory) / name).is_symlink():
                raise ValueError('Release symlinks are not allowed')
        actual.update((Path(directory) / name).relative_to(path).as_posix() for name in files)
    try:
        manifest = json.loads((path / 'manifest.json').read_text())
        _commit(manifest['source_commit'])
        if manifest['release_id'] != manifest['source_commit'] or manifest['format'] != 1:
            raise ValueError('Invalid release identity')
        files = manifest['files']
        if not isinstance(files, dict) or not files or actual != set(files) | {'manifest.json'}:
            raise ValueError('Release contains missing or unmanifested files')
        for name, digest in files.items():
            parts = Path(name).parts
            if Path(name).is_absolute() or '..' in parts or name != Path(name).as_posix():
                raise ValueError('Unsafe manifest path')
            if _digest(path / name) != digest:
                raise ValueError('Release hash mismatch: ' + name)
    except (KeyError, TypeError, OSError, json.JSONDecodeError) as error:
        raise ValueError('Invalid release manifest') from error
    return manifest


def stage_release(source_root, channel, source_commit):
    """Stage an immutable code snapshot, preserving Python's package layout."""
    source_commit = _commit(source_commit)
    source_root = Path(source_root).absolute()
    files = _code_files(source_root)
    manifest = {'format': 1, 'release_id': source_commit, 'source_commit': source_commit,
                'files': {name: _digest(path) for name, path in files.items()}}
    channel = _channel(channel)
    destination = channel / 'releases' / source_commit
    with _locked(channel):
        if destination.exists() or destination.is_symlink():
            if validate_release(destination) != manifest:
                raise ValueError('Existing release differs; use a new verified commit')
            return destination
        temporary = Path(tempfile.mkdtemp(prefix='.stage-', dir=channel / 'releases'))
        try:
            for name, source in files.items():
                target = temporary / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target, follow_symlinks=False)
            _json_write(temporary / 'manifest.json', manifest)
            validate_release(temporary)
            for name in list(files) + ['manifest.json']:
                (temporary / name).chmod(0o444)
            for directory, _, _ in os.walk(temporary, topdown=False):
                Path(directory).chmod(0o555)
            os.rename(temporary, destination)
        finally:
            if temporary.exists():
                for directory, _, _ in os.walk(temporary):
                    Path(directory).chmod(0o755)
                shutil.rmtree(temporary)
    return destination


def _pointer_id(channel, name):
    pointer = channel / name
    if not pointer.is_symlink():
        if pointer.exists():
            raise ValueError(name + ' exists and is not a release symlink')
        return None
    target = os.readlink(pointer)
    release_id = Path(target).name
    _commit(release_id)
    if target != 'releases/' + release_id:
        raise ValueError('Unexpected release pointer target')
    validate_release(channel / 'releases' / release_id)
    return release_id


def _point(channel, name, release_id):
    temporary = channel / ('.' + name + '-' + uuid.uuid4().hex)
    try:
        temporary.symlink_to('releases/' + release_id)
        os.replace(temporary, channel / name)
    finally:
        temporary.unlink(missing_ok=True)


def _promote(channel, release_id):
    validate_release(channel / 'releases' / release_id)
    old = _pointer_id(channel, 'current')
    previous = _pointer_id(channel, 'previous')
    receipt = {'release_id': release_id, 'previous_release': old,
               'changed': old != release_id}
    if old == release_id:
        receipt['previous_release'] = previous
        return receipt
    if old is not None:
        _point(channel, 'previous', old)
    _point(channel, 'current', release_id)
    _json_write(channel / 'receipt.json', receipt)
    return receipt


def promote_release(channel, release_id):
    """Atomically switch current only after both release identities validate."""
    release_id = _commit(release_id)
    channel = _channel(channel)
    with _locked(channel):
        return _promote(channel, release_id)


def rollback_release(channel):
    """Switch to the retained previous release; never deletes either snapshot."""
    channel = _channel(channel)
    with _locked(channel):
        previous = _pointer_id(channel, 'previous')
        if previous is None:
            raise ValueError('No previous release is available')
        return _promote(channel, previous)


def release_info(engine_root):
    """Explicit version metadata for request wrappers; does not import runtime."""
    manifest = validate_release(Path(engine_root).resolve())
    return {key: manifest[key] for key in ('release_id', 'source_commit')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    stage = commands.add_parser('stage')
    stage.add_argument('--source-root', required=True)
    stage.add_argument('--channel', required=True)
    stage.add_argument('--source-commit', required=True)
    for name in ('promote', 'rollback', 'status'):
        command = commands.add_parser(name)
        command.add_argument('--channel', required=True)
        if name == 'promote':
            command.add_argument('--release-id', required=True)
    args = parser.parse_args(argv)
    if args.command == 'stage':
        result = {'release_path': str(stage_release(args.source_root, args.channel, args.source_commit))}
    elif args.command == 'promote':
        result = promote_release(args.channel, args.release_id)
    elif args.command == 'rollback':
        result = rollback_release(args.channel)
    else:
        result = release_info(Path(args.channel) / 'current')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
