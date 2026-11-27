"""Authoritative forward reservations. Install behind protected release control only."""
from dataclasses import dataclass
import re
import sqlite3
import secrets
import time

MIN_FRESH_SECONDS = 0
MAX_FRESH_SECONDS = 120
PREFIX = {'base':'assertion-memory-v', 'visual':'assertion-memory-visual-v'}
VERSION = r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})'


class Reject(ValueError):
    pass


def require(value, message):
    if not value:
        raise Reject(message)


@dataclass(frozen=True)
class Candidate:
    family: str
    tag: str
    revision: str

    def version(self):
        prefix = PREFIX.get(self.family)
        require(prefix is not None and type(self.tag) is str and self.tag.startswith(prefix), 'Wrong release family')
        found = re.fullmatch(VERSION, self.tag[len(prefix):])
        require(found is not None and type(self.revision) is str and re.fullmatch('[0-9a-f]{40}', self.revision), 'Unqualified release identity')
        return tuple(map(int, found.groups()))


@dataclass(frozen=True)
class Artifact:
    candidate: Candidate
    release_id: int
    digest: str

    def checked(self):
        require(type(self.candidate) is Candidate, 'Exact candidate required')
        self.candidate.version()
        require(type(self.release_id) is int and self.release_id > 0 and type(self.digest) is str
                and re.fullmatch('[0-9a-f]{64}', self.digest), 'Exact immutable release and payload required')


@dataclass(frozen=True)
class Witness:
    """Emitted only by trusted actual deployed service smoke after protected activation."""
    origin: str
    stage: str
    artifact: Artifact
    generation: str
    observed_at: int
    native_attempt: str | None = None


@dataclass(frozen=True)
class FixApproval:
    """Derived from protected human review and a saved failure of the exact TEST pin."""
    predecessor: Artifact
    fix: Candidate
    failing_check_id: int
    review_id: int
    scope_manifest_sha256: str
    classification: str  # Includes the reviewer's explicit no-unrelated-changes decision.


@dataclass(frozen=True)
class TestRecovery:
    """Created only by an installed protected recovery verifier."""
    artifact: Artifact
    attempt_id: str
    decision_id: str
    basis: str


class Ledger:
    """Single durable writer authority. No network effects; trust the enclosing receiver.

    Callers must serialize receiver operations with this authority; a returned
    admission is not a bearer token. Never load external JSON as Witness.
    """
    def __init__(self, db: sqlite3.Connection, clock=time.time):
        require(not db.in_transaction, 'Requires clean authority connection')
        self.db = db
        self.clock = clock
        db.execute('''CREATE TABLE IF NOT EXISTS forward_train(
            family TEXT PRIMARY KEY CHECK(family IN ('base','visual')),
            baseline_tag TEXT NOT NULL, baseline_rev TEXT NOT NULL, baseline_id INTEGER NOT NULL,
            baseline_digest TEXT NOT NULL, baseline_gen TEXT NOT NULL,
            open_tag TEXT, open_rev TEXT, open_id INTEGER, open_digest TEXT,
            tested_gen TEXT, promotion_pending INTEGER NOT NULL DEFAULT 0)''')

        db.execute('''CREATE TABLE IF NOT EXISTS forward_test_attempt(
            family TEXT NOT NULL, tag TEXT NOT NULL, revision TEXT NOT NULL,
            release_id INTEGER NOT NULL, digest TEXT NOT NULL, generation TEXT,
            attempt_id TEXT NOT NULL, continued INTEGER NOT NULL DEFAULT 0, recovery_decision_id TEXT,
            PRIMARY KEY(family,tag,revision,release_id,digest))''')

    @staticmethod
    def _attempt_identity(artifact):
        c=artifact.candidate
        return (c.family,c.tag,c.revision,artifact.release_id,artifact.digest)

    def begin_test_activation(self, artifact):
        """True only for an unattempted exact candidate, persisted before native effects."""
        artifact.checked()
        c=artifact.candidate
        def begin():
            _,_,current,pin,tested,promoting=self._read(c.family)
            require(not promoting and current == c and pin == (artifact.release_id,artifact.digest),
                    'Exact pre-promotion artifact required')
            # A pre-existing published test on an older ledger adapter is
            # already attempted even if it predates the new journal.
            outcome=self.db.execute('''INSERT OR IGNORE INTO forward_test_attempt
                (family,tag,revision,release_id,digest,generation,attempt_id) VALUES(?,?,?,?,?,?,?)''',
                (*self._attempt_identity(artifact),tested,secrets.token_hex(32)))
            return tested is None and outcome.rowcount==1
        return self._write(begin)

    def exact_attempt_id(self, artifact):
        """Read the authority-issued token; callers hold the component lock."""
        artifact.checked()
        row=self.db.execute('''SELECT attempt_id FROM forward_test_attempt WHERE
            (family,tag,revision,release_id,digest)=(?,?,?,?,?)''',self._attempt_identity(artifact)).fetchone()
        require(row is not None and re.fullmatch('[0-9a-f]{64}',row[0]), 'Missing native TEST attempt identity')
        return row[0]

    def continue_unentered_test(self, artifact, recovered, *, new_native_attempt=False):
        """Consume one independently approved exact recovery by compare-and-swap.

        Baseline observation, an absent activation journal, a timeout, and
        unsigned receiver inputs cannot establish actuator non-entry.
        """
        artifact.checked()
        c=artifact.candidate
        require(type(recovered) is TestRecovery and recovered.artifact == artifact and
                recovered.basis in ('verified_actuator_never_entered','protected_manual_recovery') and
                type(recovered.attempt_id) is str and re.fullmatch('[0-9a-f]{64}',recovered.attempt_id) and
                type(recovered.decision_id) is str and re.fullmatch('[0-9a-f]{64}',recovered.decision_id),
                'Missing exact protected TEST recovery decision')
        def consume():
            _,_,current,pin,tested,promoting=self._read(c.family)
            require(not promoting and tested is None and current==c and pin==(artifact.release_id,artifact.digest),
                    'Only the still-current pending TEST candidate is recoverable')
            changed=self.db.execute('''UPDATE forward_test_attempt SET continued=1,recovery_decision_id=?,attempt_id=? WHERE
                (family,tag,revision,release_id,digest)=(?,?,?,?,?) AND attempt_id=?
                AND continued=0 AND generation IS NULL''',
                (recovered.decision_id,secrets.token_hex(32) if new_native_attempt else recovered.attempt_id,
                 *self._attempt_identity(artifact),recovered.attempt_id))
            require(changed.rowcount==1,'TEST recovery proof stale, consumed, or unknown')
        self._write(consume)

    def reconcile_test_activation(self, artifact, observed, *, fence_native=False):
        """Only fresh protected actual-service evidence may close an exact attempt."""
        self._witness(observed,'test')
        require(observed.artifact==artifact, 'Actual TEST did not prove exact attempted artifact')
        c=artifact.candidate
        def reconcile():
            _,_,current,pin,tested,promoting=self._read(c.family)
            require(not promoting and current==c and pin==(artifact.release_id,artifact.digest),
                    'Test attempt was superseded or already promoting')
            identity=self._attempt_identity(artifact)
            if fence_native:
                require(observed.native_attempt==self.exact_attempt_id(artifact),
                        'Actual native TEST attempt differs from the reserved attempt')
            row=self.db.execute('''SELECT generation FROM forward_test_attempt WHERE
                (family,tag,revision,release_id,digest)=(?,?,?,?,?)''',identity).fetchone()
            require(row is not None and row[0] in (None,observed.generation)
                    and tested in (None,observed.generation), 'Actual TEST generation does not match the attempt')
            self.db.execute('''UPDATE forward_test_attempt SET generation=? WHERE
                (family,tag,revision,release_id,digest)=(?,?,?,?,?)''',(observed.generation,*identity))
            self.db.execute('UPDATE forward_train SET tested_gen=? WHERE family=?',(observed.generation,c.family))
        return self._write(reconcile)

    def _read(self, family):
        row = self.db.execute('''SELECT baseline_tag,baseline_rev,baseline_id,baseline_digest,baseline_gen,
                              open_tag,open_rev,open_id,open_digest,tested_gen,promotion_pending FROM forward_train WHERE family=?''',
                              (family,)).fetchone()
        require(row is not None, 'No reviewed legacy baseline')
        closed = Artifact(Candidate(family,*row[:2]), *row[2:4])
        if row[5] is None:
            return closed, row[4], None, None, None, False
        return closed, row[4], Candidate(family,*row[5:7]), row[7:9], row[9], bool(row[10])

    def _write(self, fn):
        require(not self.db.in_transaction, 'Authority transaction already active')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            result = fn()
            self.db.commit()
            return result
        except BaseException:
            self.db.rollback()
            raise

    def _witness(self, w, stage):
        require(type(w) is Witness and w.origin == 'forward' and w.stage == stage
                and type(w.artifact) is Artifact, 'Fresh forward service evidence required')
        w.artifact.checked()
        require(type(w.observed_at) is int and MIN_FRESH_SECONDS <= int(self.clock()) - w.observed_at <= MAX_FRESH_SECONDS
                and type(w.generation) is str and re.fullmatch('[0-9a-f]{64}', w.generation),
                'Stale or malformed actual service evidence')

    def initialize(self, live):
        self._witness(live, 'prod')
        a, c = live.artifact, live.artifact.candidate
        require(c.version()[:2] == (0,1), 'Only reviewed running 0.1.x baseline can initialize')
        return self._write(lambda: self.db.execute(
            'INSERT INTO forward_train(family,baseline_tag,baseline_rev,baseline_id,baseline_digest,baseline_gen) VALUES(?,?,?,?,?,?)',
            (c.family,c.tag,c.revision,a.release_id,a.digest,live.generation)).rowcount)

    def reserve(self, candidate, live=None, *, fix=None, artifact=None):
        major, minor, patch = candidate.version()
        if artifact is not None:
            require(type(artifact) is Artifact and artifact.candidate == candidate, 'Wrong replacement artifact')
            artifact.checked()
        def write():
            closed, generation, current, pin, tested, promoting = self._read(candidate.family)
            if current == candidate:
                return False
            if current is None:
                self._witness(live, 'prod')
                require(live.artifact == closed and live.generation == generation, 'Live PROD does not match prior verified train')
                require((major,minor,patch) == (closed.candidate.version()[0]+1,0,0), 'Requires exactly next major at x.0.0')
            else:
                require(not promoting, 'Uncertain or in-flight PROD cannot be displaced')
                require(type(fix) is FixApproval and fix.fix == candidate and fix.classification == 'open_major_defect_only'
                        and type(fix.predecessor) is Artifact and fix.predecessor.candidate == current
                        and pin == (fix.predecessor.release_id,fix.predecessor.digest)
                        and type(fix.failing_check_id) is int and fix.failing_check_id > 0
                        and type(fix.review_id) is int and fix.review_id > 0
                        and type(fix.scope_manifest_sha256) is str
                        and re.fullmatch('[0-9a-f]{64}', fix.scope_manifest_sha256),
                        'Next minor requires reviewed exact-head repair for recorded in-train TEST failure')
                fix.predecessor.checked()
                m,n,_ = current.version()
                require((major,minor,patch) == (m,n+1,0) and candidate.revision != current.revision,
                        'Open train permits only the next minor with a new revision')
            self.db.execute('''UPDATE forward_train SET open_tag=?,open_rev=?,open_id=?,open_digest=?,tested_gen=NULL
                               WHERE family=?''', (candidate.tag,candidate.revision,artifact.release_id if artifact else None,artifact.digest if artifact else None,candidate.family))
            return True
        return self._write(write)

    def bind_release_id(self, candidate, release_id):
        require(type(release_id) is int and release_id > 0, 'Immutable release ID required')
        def bind():
            _,_,current,pin,_,_ = self._read(candidate.family)
            require(current == candidate and pin[0] in (None, release_id), 'Wrong immutable published release')
            self.db.execute('UPDATE forward_train SET open_id=? WHERE family=?',(release_id,candidate.family))
        return self._write(bind)

    def is_reserved(self, candidate):
        def check():
            _,_,current,_,_,_ = self._read(candidate.family)
            return current == candidate
        return self._write(check)

    def require_bound(self, artifact):
        artifact.checked()
        c = artifact.candidate
        def check():
            _,_,current,pin,_,promoting = self._read(c.family)
            require(not promoting and current == c and pin == (artifact.release_id,artifact.digest),
                    'Exact pre-promotion artifact required')
        return self._write(check)

    def bind_artifact(self, artifact):
        artifact.checked()
        c = artifact.candidate
        def write():
            _,_,current,pin,_,_ = self._read(c.family)
            require(current == c, 'Unreserved or obsolete release')
            require(pin in ((None,None), (artifact.release_id,None), (artifact.release_id,artifact.digest)), 'Attempt to replace immutable candidate')
            self.db.execute('UPDATE forward_train SET open_id=?,open_digest=? WHERE family=?', (artifact.release_id,artifact.digest,c.family))
        return self._write(write)

    def witness(self, w):
        require(type(w) is Witness and w.stage in ('test','prod'), 'Unknown service stage')
        self._witness(w,w.stage)
        a,c = w.artifact,w.artifact.candidate
        def write():
            _,_,current,pin,tested,promoting = self._read(c.family)
            require(current == c and pin == (a.release_id,a.digest), 'Not the exact active train artifact')
            if w.stage == 'test':
                require(not promoting, 'Cannot change TEST witness while promoting')
                self.db.execute('UPDATE forward_train SET tested_gen=? WHERE family=?',(w.generation,c.family))
            else:
                require(tested is not None and promoting, 'No authorized promotion of exact TEST artifact')
                self.db.execute('''UPDATE forward_train SET baseline_tag=?,baseline_rev=?,baseline_id=?,baseline_digest=?,baseline_gen=?,
                                open_tag=NULL,open_rev=NULL,open_id=NULL,open_digest=NULL,tested_gen=NULL,promotion_pending=0 WHERE family=?''',
                                (c.tag,c.revision,a.release_id,a.digest,w.generation,c.family))
        return self._write(write)

    def begin_promotion(self, test, *, receipt_attempt=None):
        self._witness(test, 'test')
        a,c = test.artifact,test.artifact.candidate
        def read():
            _,_,current,pin,tested,promoting = self._read(c.family)
            require(not promoting and current == c and pin == (a.release_id,a.digest) and tested == test.generation,
                    'Promotion needs current exact TEST-verified artifact')
            if receipt_attempt is not None:
                require(type(receipt_attempt) is str and re.fullmatch('[0-9a-f]{64}',receipt_attempt)
                        and test.native_attempt==receipt_attempt==self.exact_attempt_id(a),
                        'Superseded or unbound native TEST receipt')
            self.db.execute('UPDATE forward_train SET promotion_pending=1 WHERE family=?',(c.family,))
        return self._write(read)
