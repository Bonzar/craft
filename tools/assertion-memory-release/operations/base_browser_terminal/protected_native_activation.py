"""Exact one-run configured native activation using existing writer identity."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import time

import base_payload
from configured_terminal_state import _release,encode
from foreground_terminal import restore_precommit
from native_boundary import PROD_ROOT
from native_checkpoint import checkpoint_for
from operator_foreground_controller import FILES,SETTLED,_get
from release_core import ActivationWitness
from root_phase_binding import _read_root_phase,_safe_parent,document
from root_run_records import RECORDS,record
from root_terminal_publisher import canonical_terminal
from settled_guard_binding import _file
from terminal_lifecycle import TerminalRequest
from transition_witness import Admission

CONFIG=Path('/etc/assertion-release/production')
PLAN=CONFIG/'base-terminal-approved-foreground-v1.json'

def _approved(admission):
    _safe_parent(PLAN.parent)
    body=_file(PLAN,4096,owner=0,mode=0o644)
    record=json.loads(body)
    if (type(record) is not dict or set(record)!={'schema','run','lane','candidate','prior'}
            or type(record['schema']) is not int or record['schema']!=1 or record['lane']!='prod'
            or record['run']!=admission.run or _release(record['candidate'])!=admission.candidate
            or encode(record)!=body):
        raise ValueError('protected foreground run is not approved')
    return _release(record['prior'])

def _step(parent,filename,decode):
    until=time.monotonic()+20
    while time.monotonic()<until:
        value=_get(parent,filename)
        if value is not None:
            decode(value)
            return
        time.sleep(.25)
    raise TimeoutError('protected foreground witness was not recorded')

def _parent():
    _safe_parent(RECORDS)
    return os.open(RECORDS,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)

def _paths(request):
    root=PROD_ROOT/'releases'
    return tuple(root/(r.revision+'-'+r.archive) for r in (request.predecessor,request.admission.candidate))

def _require_native_pair(root,old,candidate,req):
    if root!=PROD_ROOT or (old,candidate)!=_paths(req):
        raise ValueError('protected foreground native pair differs')

def _active_precommit(request):
    parent=_parent()
    try:_step(parent,record(request.admission,FILES[request.phase]).name,lambda body:restore_precommit(body,request))
    finally:os.close(parent)

def _settled(request):
    selected=request.admission.candidate if request.phase=='restart' else request.predecessor
    exact=canonical_terminal(request,selected)
    def verify(body):
        if body!=exact:raise ValueError('wrong protected foreground settlement')
    parent=_parent()
    try:_step(parent,record(request.admission,SETTLED[request.phase]).name,verify)
    finally:os.close(parent)

def witness_for(admission,prior):
    original=TerminalRequest(admission,prior,'restart')
    def before(root,phase,old,new):
        req=TerminalRequest(admission,prior,phase)
        _require_native_pair(root,old,new,req)
        return _active_precommit(req)
    def after(root,phase,old,new):
        req=TerminalRequest(admission,prior,phase)
        _require_native_pair(root,old,new,req)
        return _settled(req)
    def recover(root):
        if root!=PROD_ROOT:raise ValueError('wrong recovery root')
        try: wire=_read_root_phase(admission)
        except FileNotFoundError:return None
        options=[TerminalRequest(admission,prior,p) for p in ('restart','restore')]
        req=next((req for req in options if document(req)==wire),None)
        if req is None:raise ValueError('foreign foreground recovery')
        _active_precommit(req)
        return lambda:_settled(req)
    return ActivationWitness(before,after,recover,before_effect=checkpoint_for(original))

def activate_terminal(revision,archive,stream,*,admission):
    if (os.getuid()!=986 or os.geteuid()!=986 or type(admission) is not Admission
            or (revision,archive)!=(admission.candidate.revision,admission.candidate.archive)):
        raise ValueError('unmatched protected Native admission')
    prior=_approved(admission)
    base_payload.deploy(stream,revision,archive,root=PROD_ROOT,config=CONFIG,
                        witness_factory=lambda _inherited:witness_for(admission,prior))
    return {'status':'ok','environment':'production','revision':revision,'sha256':archive}
