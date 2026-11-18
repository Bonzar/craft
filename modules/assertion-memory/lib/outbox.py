"""Durable local raw-message delivery. Cursors and queued messages commit together."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def watching_marker(root, session):
    return Path(root) / 'inactive' / hashlib.sha256(session.encode()).hexdigest()


def set_watching(root, session, is_watching):
    marker = watching_marker(root, session)
    if is_watching:
        marker.unlink(missing_ok=True)
        return
    # SessionEnd must not wait for a transcript reader holding the SQLite write lock.
    marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker.touch(mode=0o600, exist_ok=True)


class Outbox:
    def __init__(self, path):
        path = Path(path)
        self.root = path.parent
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=5)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS artifacts (artifact_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,data BLOB NOT NULL,receipt TEXT,attempts INTEGER NOT NULL DEFAULT 0,error TEXT);
            CREATE TABLE IF NOT EXISTS message_artifacts (session_id TEXT,message_id TEXT,artifact_id TEXT,
                PRIMARY KEY(session_id,message_id,artifact_id));
            CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS occurrences (session_id TEXT, locator TEXT, message_id TEXT NOT NULL,
                PRIMARY KEY(session_id,locator));
            CREATE TABLE IF NOT EXISTS messages (session_id TEXT, message_id TEXT, position INTEGER,
                arguments TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, error TEXT, receipt TEXT,
                PRIMARY KEY(session_id,message_id));
        ''')
        from .frontier import initialize
        initialize(self.db)

    def close(self): self.db.close()

    def session(self, session):
        row = self.db.execute('SELECT data FROM sessions WHERE session_id=?', (session,)).fetchone()
        return (json.loads(row['data']) | {'watching': not watching_marker(self.root, session).exists()}) if row else None

    def register_session(self, value):
        # Concurrent global/project hooks must never replace a worker's newer cursor.
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO sessions VALUES (?,?)',
                (value['session_id'], encoded(value)))
        return self.session(value['session_id'])

    def save_session(self, value):
        self.db.execute('INSERT INTO sessions VALUES (?,?) ON CONFLICT(session_id) DO UPDATE SET data=excluded.data',
            (value['session_id'], encoded({key: item for key, item in value.items() if key != 'watching'})))

    def set_watching(self, session, is_watching):
        set_watching(self.root, session, is_watching)

    def sessions(self):
        return [json.loads(row['data']) | {'watching': not watching_marker(self.root, row['session_id']).exists()}
            for row in self.db.execute('SELECT session_id,data FROM sessions ORDER BY session_id')]

    def occurrence_id(self, session, locator, provider_id=None):
        row = self.db.execute('SELECT message_id FROM occurrences WHERE session_id=? AND locator=?', (session, locator)).fetchone()
        if row: return row['message_id']
        message_id = provider_id or 'occurrence:' + str(uuid.uuid4())
        self.db.execute('INSERT INTO occurrences VALUES (?,?,?)', (session, locator, message_id))
        return message_id

    def store_artifact(self,image):
        from .artifacts import describe
        meta=describe(image['data'],image['metadata']['mime_type'])
        if meta!=image['metadata']:raise ValueError('Image metadata does not match bytes')
        old=self.db.execute('SELECT * FROM artifacts WHERE artifact_id=?',(meta['artifact_id'],)).fetchone()
        if old:
            if bytes(old['data'])!=image['data'] or old['mime_type']!=meta['mime_type']:raise ValueError('Immutable artifact conflict')
        else:self.db.execute('INSERT INTO artifacts(artifact_id,sha256,mime_type,size_bytes,data) VALUES (?,?,?,?,?)',
            (meta['artifact_id'],meta['sha256'],meta['mime_type'],meta['size_bytes'],image['data']))
        return meta['artifact_id']

    def enqueue(self, arguments, *, local_position=None):
        artifact_ids=arguments.get('artifact_ids',[])
        if not isinstance(artifact_ids,list) or len(artifact_ids)>8 or len(set(artifact_ids))!=len(artifact_ids):raise ValueError('Invalid local artifact references')
        for aid in artifact_ids:
            if not self.db.execute('SELECT 1 FROM artifacts WHERE artifact_id=?',(aid,)).fetchone():raise ValueError('Missing local artifact bytes')
        body = encoded(arguments)
        fingerprint = hashlib.sha256(body.encode()).hexdigest()
        old = self.db.execute('SELECT fingerprint FROM messages WHERE session_id=? AND message_id=?',
            (arguments['session_id'], arguments['message_id'])).fetchone()
        if old:
            if old['fingerprint'] != fingerprint:
                raise ValueError('Immutable native message identity has conflicting content/provenance')
            return False
        self.db.execute('INSERT INTO messages(session_id,message_id,position,arguments,fingerprint) VALUES (?,?,?,?,?)',
            (arguments['session_id'], arguments['message_id'], local_position if local_position is not None else arguments.get('position'), body, fingerprint))
        for aid in artifact_ids:self.db.execute('INSERT OR IGNORE INTO message_artifacts VALUES (?,?,?)',(arguments['session_id'],arguments['message_id'],aid))
        from .frontier import reserve
        reserve(self.db,arguments['session_id'],arguments['message_id'])
        return True

    def messages(self, session):
        rows = self.db.execute('SELECT * FROM messages WHERE session_id=? ORDER BY position,message_id', (session,))
        return [dict(row) | {'arguments': json.loads(row['arguments']),
                            'receipt': json.loads(row['receipt']) if row['receipt'] else None} for row in rows]

    def summary(self, session):
        state = self.session(session) or {}
        row = self.db.execute("SELECT COUNT(*) AS total, SUM(status='pending') AS pending, SUM(status='acknowledged') AS acknowledged, SUM(error IS NOT NULL) AS errors FROM messages WHERE session_id=?", (session,)).fetchone()
        transport_row = self.db.execute('SELECT data FROM transport_streams WHERE session_id=?',(session,)).fetchone()
        transport = json.loads(transport_row['data']) if transport_row else {}
        pending_receipts = self.db.execute('SELECT COUNT(*) FROM delivery_sequences WHERE session_id=? AND receipt IS NULL',(session,)).fetchone()[0]
        return {'queued_pending': row['pending'] or 0, 'pending_delivery_receipts':pending_receipts,
                'artifact_upload_pending':self.db.execute('SELECT COUNT(DISTINCT m.artifact_id) FROM message_artifacts m LEFT JOIN artifacts a USING(artifact_id) WHERE m.session_id=? AND a.receipt IS NULL',(session,)).fetchone()[0],
                'remote_transport':{key:transport.get(key) for key in ('stream_id','report_seq','server_observed_seq','server_acked_through','scan_state','gap_count','lifecycle','error')}, 'acknowledged_raw': row['acknowledged'] or 0,
                'delivery_errors': row['errors'] or 0, 'source_gaps': sorted(set(state.get('gaps', []))),
                'retained_source_gap_count': len(state.get('gaps', [])),
                'intentional_exclusions': state.get('exclusions', {}),
                'blocked': state.get('blocked'),
                'pending_source_proof': bool((state.get('turn') or {}).get('pending')
                    and not (state.get('turn') or {}).get('pending', {}).get('emitted'))}

    def deliver(self, sender, limit=100):
        result = {'attempted': 0, 'acknowledged': 0}
        rows = self.db.execute("SELECT * FROM messages WHERE status='pending' ORDER BY session_id,position LIMIT ?", (limit,)).fetchall()
        for row in rows:
            arguments = json.loads(row['arguments'])
            result['attempted'] += 1
            receipt, error = None, None
            try:
                receipt = sender(arguments)
                event = receipt.get('event', {}) if isinstance(receipt, dict) else {}
                mid = event.get('message_id') or event.get('metadata', {}).get('message_id')
                if (not isinstance(event.get('id'), str) or not event['id']
                        or event.get('session_id') != arguments['session_id']
                        or mid != arguments['message_id'] or event.get('source') != arguments['source']):
                    raise ValueError('Unmatched remote receipt')
            except Exception as failure:
                # Error bodies may contain credentials or raw text. Persist only a class.
                error = type(failure).__name__
            with self.db:
                if error:
                    self.db.execute('UPDATE messages SET attempts=attempts+1,error=? WHERE session_id=? AND message_id=?',
                        (error, row['session_id'], row['message_id']))
                else:
                    self.db.execute("UPDATE messages SET status='acknowledged',attempts=attempts+1,error=NULL,receipt=? WHERE session_id=? AND message_id=?",
                        (encoded(receipt), row['session_id'], row['message_id']))
                    result['acknowledged'] += 1
            if error: break  # preserve delivery order; the worker retries later
        return result
