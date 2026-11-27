"""Optional native binding for configured Base receivers.

Only an installed shared, protected operator authority can construct the
binding. No loader, permissions, credentials, process, or settings are added.
"""
from contextlib import contextmanager
from dataclasses import replace

from forward_release_checkpoints import NativeAttemptCheckpoints, NativeTestReceipt
from forward_release_receiver import protected_activation
from forward_release_train import require
from release_core import ActivationWitness


class BaseNativeBinding:
    def __init__(self, checkpoints, *, inspect_prepared, observe_test, observe_prod):
        require(type(checkpoints) is NativeAttemptCheckpoints, 'Native authority required')
        self.checkpoints=checkpoints
        self.inspect_prepared=inspect_prepared
        self.observe_test=observe_test
        self.observe_prod=observe_prod
        self.bound=None
        self.db=checkpoints.ledger.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS base_native_test_witness(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                release_id INTEGER NOT NULL, tag TEXT NOT NULL, revision TEXT NOT NULL,
                digest TEXT NOT NULL, generation TEXT NOT NULL, attempt TEXT NOT NULL, test_run INTEGER NOT NULL)''')

    @contextmanager
    def bind(self, target, attempt, test_run):
        require(self.bound is None, 'Already bound native TEST activation')
        self.bound=(target,attempt,test_run)
        try:
            yield
        finally:
            self.bound=None

    def _durable_witness(self, root, phase, prior, target):
        # Called only by release_core after native switch, smoke, journal unlink
        # and native directory fsync. A restore must not create forward proof.
        if phase!='restart':
            return
        require(self.bound is not None and self.bound[0]==target, 'Unbound native TEST switch')
        artifact=self.inspect_prepared(target)
        seen=self.observe_test(artifact)
        self.checkpoints.ledger._witness(seen,'test')
        require(seen.artifact==artifact, 'Wrong active Base TEST release at native commit')
        l=self.checkpoints.ledger
        def commit():
            _,_,active,digest,_,promoting=l._read('base')
            require(not promoting and active==artifact.candidate
                    and digest==(artifact.release_id,artifact.digest)
                    and l.exact_attempt_id(artifact)==self.bound[1], 'Native TEST attempt superseded')
            self.db.execute('''INSERT INTO base_native_test_witness VALUES(1,?,?,?,?,?,?,?)
                 ON CONFLICT(singleton) DO UPDATE SET release_id=excluded.release_id,tag=excluded.tag,
                 revision=excluded.revision,digest=excluded.digest,generation=excluded.generation,attempt=excluded.attempt,test_run=excluded.test_run''',
                 (artifact.release_id,artifact.candidate.tag,artifact.candidate.revision,
                  artifact.digest,seen.generation,self.bound[1],self.bound[2]))
        l._write(commit)

    def witness_factory(self, _lane_lock):
        def before(_root, phase, _old, target):
            if phase=='restart':
                require(self.bound is not None and self.bound[0]==target,
                        'Native TEST attempt must enclose the existing actuator')
        # Never re-execute native effects on missing/ambiguous prior witness.
        return ActivationWitness(before,self._durable_witness,lambda _root:None)

    def exact_active_test(self, artifact):
        current=self.observe_test(artifact)
        self.checkpoints.ledger._witness(current,'test')
        require(current.artifact==artifact, 'Base TEST no longer runs this exact archive')
        row=self.db.execute('''SELECT release_id,tag,revision,digest,generation,attempt
                              FROM base_native_test_witness WHERE singleton=1''').fetchone()
        c=artifact.candidate
        require(row is not None and row[:5]==(artifact.release_id,c.tag,c.revision,artifact.digest,current.generation)
                and row[5]==self.checkpoints.ledger.exact_attempt_id(artifact),
                'No exact current durable Base TEST witness')
        return replace(current,native_attempt=row[5])

    def native_receipt(self, artifact, verified_test_run):
        require(type(verified_test_run) is int and verified_test_run>0,
                'Missing authenticated selected TEST provider run')
        w=self.exact_active_test(artifact)
        row=self.db.execute('SELECT test_run FROM base_native_test_witness WHERE singleton=1').fetchone()
        require(row==(verified_test_run,), 'Selected provider receipt names an earlier native TEST attempt')
        return NativeTestReceipt(artifact,w.generation,w.native_attempt)

    def test_hook(self, request, *, dispatch, release, live_prod, review):
        return protected_activation(self.checkpoints,request,receiver_stage='test',verify_dispatch=dispatch,
              verify_release=release,identify_prepared=self.inspect_prepared,probe=lambda stage,a:self.exact_active_test(a),
              active_test=self.exact_active_test,live_prod=live_prod,verify_fix=review,bind_native_attempt=lambda target,attempt:self.bind(target,attempt,request.run_id))

    def prod_hook(self, request, *, dispatch, release, approve, prior_receipt):
        return protected_activation(self.checkpoints,request,receiver_stage='prod',verify_dispatch=dispatch,
              verify_release=release,identify_prepared=self.inspect_prepared,probe=lambda stage,a:self.observe_prod(a),
              active_test=self.exact_active_test,verify_promote_approval=approve,
              verified_test_receipt=prior_receipt)


class ConfiguredBaseNativeBridge:
    """Join two already authenticated typed provider intents to one operator binding.

    The installed resolver must return verified existing callbacks and settings
    from trusted control, never environment flags or unsigned transport input.
    """
    def __init__(self, binding, *, resolve_test, resolve_prod, native_deploy, roots):
        require(type(binding) is BaseNativeBinding, 'Missing configured Base native binding')
        require(set(roots)=={'test','prod'}, 'Exact Base stage roots required')
        self.binding,self.resolve_test,self.resolve_prod=binding,resolve_test,resolve_prod
        self.native_deploy,self.roots=native_deploy,roots

    @staticmethod
    def _request(req, stage, tag, release_id, revision, digest, run_id):
        from forward_release_receiver import Request
        require(type(req) is Request and req.stage==stage and req.artifact.candidate.family=='base',
                'Wrong configured Base receiver lane')
        a=req.artifact
        a.checked()
        require((a.candidate.tag,str(a.release_id),a.candidate.revision,a.digest,str(req.run_id))
                ==(tag,str(release_id),revision,digest,str(run_id)), 'Mismatch with verified configured Base intent')
        return req

    def test_after_verified_intent(self,intent,stream):
        req,callback=self.resolve_test(intent)
        req=self._request(req,'test',intent.tag,intent.release_id,intent.revision,intent.archive_sha256,intent.run)
        root,config=self.roots['test']
        return self.native_deploy(stream,intent.revision,intent.archive_sha256,root=root,config=config,
                   authorize_activation=self.binding.test_hook(req,**callback),
                   witness_factory=self.binding.witness_factory)

    def prod_after_verified_intent(self,promotion,stream):
        req,callback,verified_test_run=self.resolve_prod(promotion)
        a,c=promotion.release,promotion.content
        req=self._request(req,'prod',a.tag,a.release_id,a.revision,c.sha256,promotion.run_id)
        root,config=self.roots['prod']
        return self.native_deploy(stream,a.revision,c.sha256,root=root,config=config,
                   authorize_activation=self._authorizing_prod(req,callback,verified_test_run))
        
    def _authorizing_prod(self,req,callback,verified_test_run):
        # The actual witness and its selected-run binding must be checked
        # under Checkpoints.production's same-family lock, not in the sender.
        def authorize(target,switch):
            return self.binding.prod_hook(req,prior_receipt=lambda a:self.binding.native_receipt(a,verified_test_run),
                                          **callback)(target,switch)
        return authorize
