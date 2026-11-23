"""Validate admission lineage separately from immutable business ledgers."""
import re
from .source_admission import stamp, specifications, trigger_sql

TABLES=frozenset(('memory_source_admission_clock','memory_source_admission'))


def validate(db):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not TABLES<=tables:
        raise ValueError('Admission schema missing; initialize it before starting a new rebuild')
    revision,instance=stamp(db)
    if type(revision) is not int or revision<0 or not isinstance(instance,str) or not re.fullmatch('[0-9A-F]{64}',instance):
        raise ValueError('Invalid admission clock')
    if db.execute('SELECT count(*) FROM memory_source_admission_clock').fetchone()[0]!=1:
        raise ValueError('Invalid admission clock cardinality')
    for thoughts in (False,True):
        for spec in specifications(thoughts):
            table,kind,key,version,_=spec
            if table not in tables:
                if thoughts:continue
                raise ValueError('Admission producer table missing')
            for expected in trigger_sql(spec):
                name=expected.split()[2]
                row=db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).fetchone()
                if row is None or row[0]!=expected:
                    raise ValueError('Admission trigger divergence: '+name)
            if db.execute(f'SELECT 1 FROM {table} t WHERE t.{key} IS NOT NULL AND NOT EXISTS '
                '(SELECT 1 FROM memory_source_admission a WHERE a.kind=? '
                f'AND a.identity=t.{key} AND a.version={"t."+version if version else "0"}) LIMIT 1',(kind,)).fetchone():
                raise ValueError('Admission producer marker missing')
    if db.execute("SELECT 1 FROM memory_source_admission WHERE kind NOT IN ('raw','head','version') "
                  "OR typeof(revision)!='integer' OR revision<0 OR revision>? "
                  "OR typeof(version)!='integer' OR (kind!='version' AND version!=0) "
                  "OR (kind='version' AND version<1) LIMIT 1",(revision,)).fetchone():
        raise ValueError('Invalid admission marker')
    return revision,instance


def lineage(base,current,*,instance=None,live=False):
    """No missing tombstones, revision rollback, clock reuse or transplanted epoch.

    Live capture may add new identities only; revisions of the existing baseline
    identities must remain exact (including ABA). Staged/runtime writes may
    advance existing markers, but cannot relabel a mutation as an older revision.
    """
    old,old_instance=validate(base);new,new_instance=validate(current)
    if new_instance!=(instance or old_instance) or new<old:
        raise ValueError('Admission instance or clock divergence')
    for table in TABLES:
        left=[tuple(r)[1:] for r in base.execute('PRAGMA table_info('+table+')')]
        right=[tuple(r)[1:] for r in current.execute('PRAGMA table_info('+table+')')]
        if left!=right:raise ValueError('Admission schema divergence')
    known=set()
    for kind,key,version,revision in base.execute('SELECT kind,identity,version,revision FROM memory_source_admission'):
        known.add((kind,key,version))
        row=current.execute('SELECT revision FROM memory_source_admission WHERE kind=? AND identity=? AND version=?',
                            (kind,key,version)).fetchone()
        if row is None or row[0]<revision or (live and row[0]!=revision) or (row[0]!=revision and row[0]<=old):
            raise ValueError('Admission identity revision divergence')
    for kind,key,version,revision in current.execute('SELECT kind,identity,version,revision FROM memory_source_admission'):
        if (kind,key,version) not in known and revision<=old:
            raise ValueError('Backdated admission identity')


def fork(db):
    """Called inside the stage-only writer before any stage work/handles exist."""
    revision,instance=validate(db)
    db.execute('UPDATE memory_source_admission_clock SET revision=revision+1,instance=hex(randomblob(32)) WHERE id=1')
    return {'source_instance':instance,'stage_instance':stamp(db)[1],'base_revision':revision}


def stage_lineage(state,base,stage):
    contract=state.get('admission')
    if not isinstance(contract,dict) or set(contract)!={'source_instance','stage_instance','base_revision'}:
        raise ValueError('Rebuild lacks admission lineage; start a new operation')
    if stamp(base)!=(contract['base_revision'],contract['source_instance']) or contract['stage_instance']==contract['source_instance']:
        raise ValueError('Rebuild admission origin divergence')
    lineage(base,stage,instance=contract['stage_instance'])
    if stamp(stage)[0]<=contract['base_revision']:
        raise ValueError('Rebuild stage epoch was not advanced')


def prepare_publication(state,live,stage):
    """Separate epoch plus nondecreasing counter across the atomic replacement.

    Idempotent under an interrupted cutover. No live marker is copied into the
    independently tracked stage. The stage's own triggers assigned those IDs.
    """
    live_revision,live_instance=validate(live)
    stage_revision,stage_instance=validate(stage)
    contract=state['admission']
    if live_instance!=contract['source_instance'] or stage_instance!=contract['stage_instance']:
        raise ValueError('Admission publication lineage divergence')
    floor=max(stage_revision,live_revision+1)
    stage.execute('UPDATE memory_source_admission_clock SET revision=? WHERE id=1',(floor,))
