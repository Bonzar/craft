"""Render only an explicitly witnessed scoped Graph user-unit drop-in; never apply it."""
from __future__ import annotations
import argparse
from pathlib import Path, PurePosixPath
import re

PROFILES={
    'test':('assertion-test-deploy-graph.service','/srv/assertion-release/test/current',
            '/srv/assertion-release/test/state/fictional.sqlite3','8891'),
    'prod':('assertion-production-deploy-graph.service','/srv/assertion-release/production/current',
            '/var/lib/assertion-memory/memory.sqlite3','8901'),
}
TOKEN='standalone-graph-token'
# Deliberately not the owner cookie, queue write key, or generic Graph read credential.
CRED_ROOT='/run/assertion-graph-reset-read'
DEFAULT_ARG=re.compile(r'(?:/[a-zA-Z0-9_./-]+|%h/[a-zA-Z0-9_./-]+|--[a-z-]+|[0-9]+|127\.0\.0\.1|apps\.graph_viewer\.server|[a-zA-Z0-9_.:-]+|https://[a-zA-Z0-9.-]+(?::[0-9]+)?)\Z')

def render(lane, unit, original):
    expect_unit, prefix, source, port=PROFILES[lane]
    if unit!=expect_unit or len(original)>4096 or '\r' in original:
        raise ValueError('Wrong scoped Graph unit')
    matches=[line.removeprefix('ExecStart=') for line in original.splitlines()
             if line.startswith('ExecStart=') and line!='ExecStart=']
    if len(matches)!=1:
        raise ValueError('Expected a witnessed single effective Graph ExecStart')
    command=matches[0]
    parts=command.split(' ')
    if not all(DEFAULT_ARG.fullmatch(arg) and '..' not in PurePosixPath(arg).parts for arg in parts):
        raise ValueError('Graph unit uses unsupported ExecStart syntax')
    if parts[0] not in (prefix+'/venv/bin/python','%h/current/venv/bin/python') or not (
        parts[1:4]==['-B','-m','apps.graph_viewer.server'] or parts[1:3]==['-m','apps.graph_viewer.server']):
        raise ValueError('Wrong Graph package interpreter')
    def unique_value(name):
        if parts.count(name)!=1 or parts.index(name)+1==len(parts):
            raise ValueError('Expected exactly one scoped Graph argument')
        return parts[parts.index(name)+1]
    source_args={source} | ({'%h/state/fictional.sqlite3'} if lane=='test' else set())
    if unique_value('--source') not in source_args or unique_value('--port')!=port:
        raise ValueError('Wrong Graph lane storage or listener')
    if '--host' in parts and unique_value('--host')!='127.0.0.1':
        raise ValueError('Graph readiness requires loopback')
    if any(arg.startswith(('--database-clear-','--bridge-read-token')) for arg in parts):
        raise ValueError('Review existing credential binding explicitly')
    return (f'# Private reset readiness only; {lane}, generated from reviewed effective ExecStart\n'
            '[Service]\nExecStart=\n'
            f'ExecStart={command} --database-clear-lane {lane}'
            f' --database-clear-read-token-file {CRED_ROOT}/{lane}/{TOKEN}\n')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lane',choices=tuple(PROFILES))
    parser.add_argument('unit')
    parser.add_argument('effective_unit',type=Path,help='reviewed data-only file containing its effective ExecStart')
    args=parser.parse_args()
    expected=args.effective_unit
    if expected.is_symlink() or not expected.is_file() or expected.stat().st_size>4096:
        raise SystemExit('Expected bounded reviewed effective ExecStart file')
    print(render(args.lane,args.unit,expected.read_text(encoding='ascii')),end='')

if __name__=='__main__':
    main()
