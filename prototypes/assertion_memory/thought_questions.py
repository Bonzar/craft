"""Equal question formulations using existing versioned applicability receipts."""
from __future__ import annotations
import json
from datetime import datetime,timezone
from .storage import _timestamp


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def ensure_schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS memory_thought_reuse_checks '
        '(thought_id TEXT NOT NULL,version INTEGER NOT NULL,question TEXT NOT NULL,frontier TEXT NOT NULL,'
        "proofs TEXT NOT NULL DEFAULT '[]',PRIMARY KEY(thought_id,version,question))")
    if 'proofs' not in {row[1] for row in c.execute('PRAGMA table_info(memory_thought_reuse_checks)')}:
        c.execute("ALTER TABLE memory_thought_reuse_checks ADD COLUMN proofs TEXT NOT NULL DEFAULT '[]'")


def proof_rows(c,thought_id,version):
    return [dict(row) for row in c.execute('SELECT question,frontier,proofs FROM memory_thought_reuse_checks '
        'WHERE thought_id=? AND version=?',(thought_id,version))]


def proofs(row):
    return json.loads(row['proofs']) or [{'frontier':row['frontier'],'context':None,'checked_at':None}]


def applicable(proof,context,*,first=False):
    bound=proof.get('context')
    if bound is not None:
        return bound==context
    cutoff=(context or {}).get('recorded_as_of')
    stamp=proof.get('first_checked_at',proof.get('checked_at')) if first else proof.get('checked_at')
    return cutoff is None or (stamp is not None and _timestamp(stamp)<=cutoff)


def question_texts(thought,rows,context=None):
    result={thought.question} if thought.question else set()
    result.update(row['question'] for row in rows if any(applicable(proof,context,first=True) for proof in proofs(row)))
    return sorted(result)


def checked(rows,question,frontier,context=None):
    return any(row['question']==question and any(proof['frontier']==frontier and applicable(proof,context)
        for proof in proofs(row)) for row in rows)


def save_check(c,thought,question,frontier,context=None):
    if not isinstance(question,str) or not question.strip():
        raise ValueError('applicability question must be nonempty text')
    row=c.execute('SELECT question,frontier,proofs FROM memory_thought_reuse_checks '
        'WHERE thought_id=? AND version=? AND question=?',(thought.id,thought.version,question)).fetchone()
    prior=proofs(dict(row)) if row else []
    stamp=_now()
    old=next((proof for proof in prior if proof.get('context')==context),None)
    if old is None:
        prior.append({'frontier':frontier,'context':context,'first_checked_at':stamp,'checked_at':stamp})
    else:
        old['first_checked_at']=old.get('first_checked_at') or old.get('checked_at') or stamp
        if old['frontier']!=frontier or old.get('checked_at') is None:
            old.update(frontier=frontier,checked_at=stamp)
    body=json.dumps(prior,ensure_ascii=False,sort_keys=True)
    if row and row['frontier']==frontier and row['proofs']==body:
        return False
    c.execute('INSERT INTO memory_thought_reuse_checks(thought_id,version,question,frontier,proofs) VALUES(?,?,?,?,?) '
        'ON CONFLICT(thought_id,version,question) DO UPDATE SET frontier=excluded.frontier,proofs=excluded.proofs',
        (thought.id,thought.version,question,frontier,body))
    return True


def decode_index_questions(value):
    if not value:return []
    if value.startswith('['):
        try:
            parsed=json.loads(value)
        except ValueError:
            return [value]
        if isinstance(parsed,list) and all(isinstance(item,str) for item in parsed):return parsed
    return [value]
