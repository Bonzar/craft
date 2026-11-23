"""Transactional membership generations, shared by every SQL producer.

Only identifiers and latest revisions are stored, including tombstones. No raw
text, graph projection, model work or filesystem work runs during installation.
"""

def install(db, *, thoughts=False):
    db.execute('CREATE TABLE IF NOT EXISTS memory_source_admission_clock ('
               'id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL '
               "CHECK(typeof(revision)='integer' AND revision>=0), instance TEXT NOT NULL)")
    db.execute('INSERT OR IGNORE INTO memory_source_admission_clock VALUES(1,0,hex(randomblob(32)))')
    db.execute('CREATE TABLE IF NOT EXISTS memory_source_admission ('
               'kind TEXT NOT NULL, identity TEXT NOT NULL, version INTEGER NOT NULL, '
               'revision INTEGER NOT NULL, PRIMARY KEY(kind,identity,version)) WITHOUT ROWID')
    specs = specifications(thoughts)
    for table,kind,key,version,update_when in specs:
        prefix='source_admission_'+table
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?",(prefix+'_insert',)).fetchone():
            continue
        # Migration seeds only the compact identity projection, once, atomically
        # with trigger installation. Future changes cannot inherit revision zero.
        db.execute('INSERT OR IGNORE INTO memory_source_admission '
            f'SELECT ?,{table}.{key},{table+"."+version if version else "0"},revision FROM {table},memory_source_admission_clock '
            f'WHERE {table}.{key} IS NOT NULL',(kind,))
        for statement in trigger_sql((table,kind,key,version,update_when)):
            db.execute(statement)


def specifications(thoughts=False):
    return ([('memory_thought_versions','version','thought_id','version',None),
              ('memory_thoughts','head','id',None,'OLD.id IS NOT NEW.id')]
             if thoughts else
             [('memory_raw_events','raw','id',None,None),
              ('memory_event_positions','raw','event_id',None,None),
              ('memory_capture_jobs','raw','event_id',None,
               "OLD.event_id IS NOT NEW.event_id OR (OLD.status='waiting_origin') != (NEW.status='waiting_origin')")])


def trigger_sql(spec):
    table,kind,key,version,update_when=spec
    prefix='source_admission_'+table
    for operation,aliases in [('insert',('NEW',)),('update',('OLD','NEW')),('delete',('OLD',))]:
        when=' WHEN '+update_when if operation=='update' and update_when else ''
        body='UPDATE memory_source_admission_clock SET revision=revision+1 WHERE id=1;'
        for alias in aliases:
            body+=(f'INSERT INTO memory_source_admission(kind,identity,version,revision) '
                   f"SELECT '{kind}',{alias}.{key},{alias+'.'+version if version else '0'},revision "
                   f'FROM memory_source_admission_clock WHERE {alias}.{key} IS NOT NULL '
                   'ON CONFLICT(kind,identity,version) DO UPDATE SET revision=excluded.revision;')
        yield f'CREATE TRIGGER {prefix}_{operation} AFTER {operation.upper()} ON {table}{when} BEGIN {body} END'


def stamp(db):
    row=db.execute('SELECT revision,instance FROM memory_source_admission_clock WHERE id=1').fetchone()
    if row is None:raise RuntimeError('Source admission schema unavailable')
    return row[0],row[1]


def unchanged(db, watermark, kind, identity, version=0):
    row=db.execute('SELECT revision FROM memory_source_admission WHERE kind=? AND identity=? AND version=?',
                   (kind,identity,version)).fetchone()
    return row is not None and row[0]<=watermark
