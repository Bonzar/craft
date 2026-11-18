#!/usr/bin/env python3
"""Native harness header helper. Its stdout is credentials for the harness only."""
import json
import argparse
import os
from pathlib import Path
import re
import sys


def dotenv_token(path, name):
    """Read only the named literal; never execute shell expressions from dotenv."""
    pattern = re.compile(r'''^\s*(?:export\s+)?''' + re.escape(name) +
        r'''\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<plain>[^\s#]*))(?:\s*#.*)?\s*$''')
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return ''
    for line in lines:
        match = pattern.fullmatch(line)
        if match:
            value = next(value for value in match.group('double', 'single', 'plain') if value is not None)
            if any(char in value for char in '$`'): raise ValueError('Credential must be a literal')
            return value
    return ''


def token(config):
    if config.get('bearer_token_file'):
        path = Path(config['bearer_token_file']).expanduser()
        if not path.is_absolute() or path.stat().st_mode & 0o077:
            raise ValueError('Credential file must be absolute and private (0600)')
        with path.open('r', encoding='utf-8') as stream: value = stream.read(16385).strip()
    elif config.get('bearer_token_env_var'):
        value = os.environ.get(config['bearer_token_env_var'], '')
        if not value and config.get('bearer_token_env_file'):
            value = dotenv_token(config['bearer_token_env_file'], config['bearer_token_env_var'])
    else: return None
    if not value or len(value) > 16384 or any(char.isspace() for char in value):
        raise ValueError('Credential is missing or malformed')
    return value


def main():
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('token_file', nargs='?')
        parser.add_argument('--env-var')
        parser.add_argument('--env-file')
        args = parser.parse_args()
        value = token({'bearer_token_file':args.token_file, 'bearer_token_env_var':args.env_var,
                       'bearer_token_env_file':args.env_file})
        if not value: raise ValueError('Credential missing')
        print(json.dumps({'Authorization': 'Bearer ' + value}))
    except Exception:
        print('Memory credential unavailable', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
