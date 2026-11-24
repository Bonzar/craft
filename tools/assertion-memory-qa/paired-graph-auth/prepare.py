#!/usr/bin/python3
"""Prepare an exact-baseline graph adapter patch, never modifying installed files."""
import argparse
import ast
import hashlib
import os
from pathlib import Path
import stat
import sys

BASELINE = {
    'test': '570e25d9a701d5e27520b7ef018e532c665718990b9950474f6256e9561b2eb8',
    'production': '2acf98a0d1d946bd302cfe1da2155e8857cd8cb35dda520e6154fddb626e91a6',
}
SIDE = b"DIGEST = (BASE/'sites-digest').read_text().strip()\n"
AUTH = b"    if not re.fullmatch(r'Bearer [a-f0-9]{64}',auth) or not hmac.compare_digest(hashlib.sha256(auth[7:].encode()).hexdigest(),DIGEST):\n"
IMPORT = b'import json\n'
INSERT = b'''def _paired_sites_digest(base):
    # This second digest may only come from an independent root-managed sidecar.
    try:
        fd = os.open(base/'sites-paired-digest', os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                return ''
            raw = stream.read(66)
        if not re.fullmatch(rb'[a-f0-9]{64}\\n?', raw):
            return ''
        return raw[:64].decode('ascii')
    except OSError:
        return ''


PAIRED_DIGEST = _paired_sites_digest(BASE)


def _sites_authorized(auth):
    if not re.fullmatch(r'Bearer [a-f0-9]{64}', auth):
        return False
    candidate = hashlib.sha256(auth[7:].encode()).hexdigest()
    primary = hmac.compare_digest(candidate, DIGEST)
    paired = hmac.compare_digest(candidate, PAIRED_DIGEST)
    return primary or paired

'''
SOURCE_ANCHOR = b"    raise RuntimeError('Missing ingress configuration')\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patch(original, profile):
    if sha(original) != BASELINE[profile] or any(original.count(line) != 1 for line in (IMPORT, SIDE, AUTH, SOURCE_ANCHOR)):
        raise ValueError('base digest or anchors do not match selected scope')
    result = (original.replace(IMPORT, IMPORT+b'import os\nimport stat\n', 1)
        .replace(SOURCE_ANCHOR, SOURCE_ANCHOR+INSERT, 1)
        .replace(AUTH, b'    if not _sites_authorized(auth):\n', 1))
    ast.parse(result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('profile', choices=BASELINE)
    parser.add_argument('old', type=Path)
    parser.add_argument('new', type=Path, nargs='?')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check == (args.new is not None):
        parser.error('use either --check or a new exclusive staging file')
    try:
        with os.fdopen(os.open(args.old, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('unexpected source type')
            original = stream.read(65537)
        if len(original) > 65536:
            raise ValueError('adapter too large')
        result = patch(original, args.profile)
        if not args.check:
            with os.fdopen(os.open(args.new,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb') as stream:
                stream.write(result)
                stream.flush()
                os.fsync(stream.fileno())
        print(f'{sha(original)} -> {sha(result)}')
    except (ValueError, OSError, SyntaxError) as error:
        print('Rejected: '+type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
