"""Export the exact Base operation registry without reading application data."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from . import mcp_server, operation_registry
from .storage import MemoryStore

SCHEMA_VERSION = 2
OPERATION_REGISTRY = 'base-typed-operations-v1'


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('ascii')


def sha(value):
    return hashlib.sha256(encoded(value)).hexdigest()


async def export(revision):
    if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('Expected exact Base source revision')
    with TemporaryDirectory(prefix='assertion-release-catalog-') as temp:
        db = str(Path(temp)/'blank.sqlite3')
        with MemoryStore(db) as store:
            config = mcp_server.ServiceConfig(db=db, provider='none', processing_session_ids=[])
            listing = operation_registry.create_operations(config, store).descriptors()
    tools, descriptions = {}, {}
    for name, entry in listing.items():
        schema = entry['inputSchema']
        if (not re.fullmatch(r'memory_[a-z0-9_]{1,80}', name) or name in tools
                or type(schema) is not dict or schema.get('type') != 'object'):
            raise ValueError('Unexpected canonical operation descriptor')
        tools[name] = schema
        descriptions[name] = entry['description']
    if not tools:
        raise ValueError('Empty canonical MCP catalog')
    return {'version': SCHEMA_VERSION, 'revision': revision,
            'module_sha256': sha({module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                                  for module in (mcp_server, operation_registry)}),
            'runtime_operation_registry': OPERATION_REGISTRY, 'schema_sha256': sha(tools),
            'tools': tools, 'descriptions': descriptions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    payload = encoded(asyncio.run(export(args.revision))) + b'\n'
    # New candidate only: never truncate a file or touch a DB selected by user.
    with args.output.open('xb') as output:
        output.write(payload)


if __name__ == '__main__':
    main()
