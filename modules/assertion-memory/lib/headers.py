#!/usr/bin/env python3
"""Native harness header helper. Its stdout is credentials for the harness only."""
import json
import os
from pathlib import Path
import sys


def token(config):
    if config.get('bearer_token_file'):
        path = Path(config['bearer_token_file']).expanduser()
        if not path.is_absolute() or path.stat().st_mode & 0o077:
            raise ValueError('Credential file must be absolute and private (0600)')
        with path.open('r', encoding='utf-8') as stream: value = stream.read(16385).strip()
    elif config.get('bearer_token_env_var'):
        value = os.environ.get(config['bearer_token_env_var'], '')
    else: return None
    if not value or len(value) > 16384 or any(char.isspace() for char in value):
        raise ValueError('Credential is missing or malformed')
    return value


def main():
    try:
        value = token({'bearer_token_file': sys.argv[1]})
        print(json.dumps({'Authorization': 'Bearer ' + value}))
    except Exception:
        print('Memory credential unavailable', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
