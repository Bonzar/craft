"""Guarded restoration of private immutable snapshots after a service restart."""
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote
from .common import APIError,selector
from .projection import PreparedGraph
from .providers.base import membership_checked


def private_file(path,maximum=None):
    if path.is_symlink() or not path.is_file():
        return False
    info=path.stat()
    return info.st_uid==os.getuid() and not info.st_mode&0o077 and (maximum is None or info.st_size<=maximum)


def read_private_json(path,maximum=65536):
    if not private_file(path,maximum):
        raise ValueError('Private cache descriptor unavailable')
    value=json.loads(path.read_bytes())
    if not isinstance(value,dict):
        raise ValueError('Invalid cache descriptor')
    return value


def valid_manifest(manifest,revision,provider_id,directory):
    if manifest.get('schema')!=1 or manifest.get('revision')!=revision or manifest.get('provider_id')!=provider_id:
        return False
    if not isinstance(manifest.get('preparation',{}),dict) or not isinstance(manifest.get('readiness',[]),list):
        return False
    selected=manifest.get('selector',{})
    if not isinstance(selected,dict) or type(selected.get('history')) is not bool:
        return False
    normalized=selector(dict(selected,history='1' if selected['history'] else '0'))
    if normalized!=selected:
        return False
    if selected.get('session_id') and manifest.get('preparation',{}).get('session_result_policy')!='core-run-provenance-pins-v2-request-context':
        return False
    if selected.get('session_id'):
        history=(manifest.get('session_filter') or {}).get('overview_history') or {}
        watermark=history.get('snapshot_id')
        if type(watermark) is not int or watermark<0 or manifest.get('preparation',{}).get('causal_output_snapshot_id')!=watermark:return False
        if selected.get('output_snapshot_id') is not None and selected['output_snapshot_id']!=watermark:return False
        if selected.get('run_id')!=history.get('selected_run_id'):return False
    counts=manifest.get('counts',{})
    for kind,stride in [('nodes',16),('edges',8)]:
        count=counts.get(kind);description=manifest.get(kind,{})
        if type(count) is not int or not 0<=count<=0xffffffff:
            return False
        if description.get('stride')!=stride or description.get('byte_length')!=count*stride:
            return False
        if description.get('format')!=f'gv-{kind}-le-v1' or description.get('url')!=f'/api/v1/graph/{revision}/{kind}.bin':
            return False
        if not private_file(directory/(kind+'.bin')) or (directory/(kind+'.bin')).stat().st_size!=count*stride:
            return False
    return private_file(directory/'projection.sqlite3') and private_file(directory/'topics.json')


def valid_ordinal_tables(path,counts):
    uri='file:'+quote(str(path),safe='/')+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as c:
        c.execute('PRAGMA query_only=ON')
        for name in ['nodes','edges']:
            rows,maximum=c.execute(f'SELECT count(*),coalesce(max(ordinal),-1) FROM {name}').fetchone()
            if rows!=counts[name] or maximum!=rows-1:
                return False
    return True


def restore_graph(cache,revision,provider_id):
    if not re.fullmatch(r'[0-9a-f]{32}',revision):
        return None
    directory=Path(cache)/revision
    try:
        if directory.is_symlink() or not directory.is_dir() or directory.stat().st_mode&0o077 or directory.stat().st_uid!=os.getuid():
            return None
        manifest=read_private_json(directory/'manifest.json')
        if not valid_manifest(manifest,revision,provider_id,directory) or not valid_ordinal_tables(directory/'projection.sqlite3',manifest['counts']):
            return None
        graph=PreparedGraph(directory,manifest)
        if private_file(directory/'watermarks.json',1024*1024):
            graph.watermarks=read_private_json(directory/'watermarks.json',1024*1024)
        if not membership_checked(manifest,getattr(graph,'watermarks',None)):
            manifest.setdefault('readiness',[]).append('legacy_source_membership_unchecked')
        return graph
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,APIError):
        return None
