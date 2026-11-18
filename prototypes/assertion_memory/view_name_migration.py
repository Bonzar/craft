"""Reviewed editorial naming migration, with a read-only-source planning stage.

Plan uses a consistent in-memory copy of the authorized database and the normal
configured model adapter. Apply consumes that exact accepted manifest, makes a
verified backup first, and performs only bounded append/CAS operations. It never
rebuilds knowledge or calls a model. Manifest files contain authorized private
questions/context and must be stored in the normal protected production area.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .backup import snapshot_database
from .engine import MemoryEngine
from .mcp_server import ServiceConfig
from .storage import MemoryStore
from .view_names import apply_migration, migration_plan


def run(config, *, scope, manifest_path, operation=None, view_ids=None, apply=False, backup_path=None):
    manifest_path = Path(manifest_path)
    if apply:
        manifest = json.loads(manifest_path.read_text())
        if backup_path is None:
            raise ValueError('apply requires a new verified backup path')
        if manifest.get('scope') != scope:
            raise ValueError('manifest does not match authorized scope')
        snapshot_database(Path(config.db),Path(backup_path))
        with MemoryStore(config.db) as store:
            # Apply never constructs/invokes model or embedding providers.
            return apply_migration(MemoryEngine(store),manifest,scope=scope)
    if operation is None:
        raise ValueError('plan requires a stable operation revision')
    if manifest_path.exists():
        raise FileExistsError('refusing to replace an existing reviewed manifest')
    # Even schema upgrades and metadata initialization happen only in the copy.
    with MemoryStore(':memory:') as copy:
        with closing(sqlite3.connect(Path(config.db).resolve(strict=True).as_uri()+'?mode=ro',uri=True)) as source:
            source.backup(copy._connection)
        manifest = migration_plan(config.engine(copy),scope=scope,operation=operation,view_ids=view_ids)
    # Atomic creation avoids clobbering another operator's dry-run output.
    with manifest_path.open('x') as output:
        json.dump(manifest,output,ensure_ascii=False,indent=2)
        output.write('\n')
    outcomes=[t['outcome'] for t in manifest['targets']]
    return {'dry_run':True,'target_count':manifest['target_count'],'accepted_count':outcomes.count('accepted'),
        'unresolved_count':outcomes.count('unresolved'),'out_of_scope_rejected_count':outcomes.count('out_of_scope_rejected'),
        'manifest':str(manifest_path),'complete':all(o=='accepted' for o in outcomes)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('plan','apply'))
    parser.add_argument('--config',required=True,type=Path,help='normal ServiceConfig JSON; no provider defaults')
    parser.add_argument('--scope',required=True)
    parser.add_argument('--operation',help='stable migration/editorial revision, required for plan')
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--view-id',action='append',help='optional exact authorized targets; default all definitions in scope')
    parser.add_argument('--backup',type=Path,help='new backup path, required for apply')
    args=parser.parse_args(argv)
    try:
        config=ServiceConfig(**json.loads(args.config.read_text()))
        result=run(config,scope=args.scope,manifest_path=args.manifest,operation=args.operation,
            view_ids=args.view_id,apply=args.mode=='apply',backup_path=args.backup)
    except (ValueError,TypeError,KeyError,OSError,sqlite3.Error) as error:
        parser.exit(1,'naming migration failed: '+str(error)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['complete'] else 2


if __name__=='__main__':
    raise SystemExit(main())
