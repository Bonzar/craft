"""Internal closures for the existing UID980 configured authorizer.

Not a CLI or network service. Existing configured entrypoint can opt in only
from a reviewed immutable operations bundle, with independently observed
root phase and existing one-use v3 authorization. v1/v2 path unchanged.
"""
import json
from io import BytesIO
from configured_base_prod_gate import permit,read_frame,request
from guard_lookup import REQUEST, respond
from authorizer_cas import publish,read_owned_record,acknowledge
from configured_terminal_state import (admit,commit,acknowledge_exact,pending_request,read_exact,_release)
from configured_terminal_admission import verify_admission,signed_audience
from root_terminal_publisher import dispatch_existing_authorizer

def admit_exact(*, token,issuer_keys,policy,promotion,nonce,candidate,now, consume_authorized_run):
    """Persist only after the configured authorizer's real nonce/run gate.

    A crash after consuming nonce but before file CAS fails closed. It never
    forwards the candidate or reuses a spent proof.
    """
    verified=verify_admission(token,issuer_keys,policy,promotion,nonce,candidate,now)
    before=read_owned_record()
    after=admit(before,verified_admission=verified)
    if consume_authorized_run(verified) is not True:
        raise ValueError('configured authorizer one-use gate denied')
    publish(expected=before,replacement=after)
    return verified

def terminal_frame(frame,*,policy,ledger,issuer_keys,now):
    op=frame.get('op')
    if op not in ('terminal-challenge-v3','terminal-confirm-v3'):
        raise ValueError('unsupported protected activation operation')
    challenge=op=='terminal-challenge-v3'
    expected={'op','intent','candidate'} if challenge else {'op','intent','candidate','nonce','token'}
    if set(frame)!=expected:
        raise ValueError('invalid terminal frame fields')
    candidate=_release(frame['candidate'])
    legacy={k:v for k,v in frame.items() if k!='candidate'}
    legacy['op']='challenge' if challenge else 'confirm'
    promotion,_=request(legacy)
    bound=lambda nonce:signed_audience(policy,promotion,nonce,candidate)
    if challenge:
        return ledger.issue_for(promotion,now,bound)
    nonce=frame['nonce']
    admit_exact(token=frame['token'],issuer_keys=issuer_keys(),policy=policy,
        promotion=promotion,nonce=nonce,candidate=candidate,now=now,
        consume_authorized_run=lambda _:ledger.consume_for(promotion,nonce,bound(nonce),now))
    return permit(promotion)

def serve_exact(peer, *, normal, independently_observed_phase, protected_marker_active=lambda:False,
                sealed_terminal_dispatch=None, terminal_projection_enabled=False):
    if type(terminal_projection_enabled) is not bool:
        raise ValueError('exact projection selection required')
    def projection(raw,uid,stream):
        if not terminal_projection_enabled:
            return None
        from terminal_projection import dispatch,PUBLISH,READ
        from projection_persistence import read_projection,persist_projection,read_locked_view
        # Protocol selection does not grant capability: dispatch validates
        # peer UID, exact canonical fields, active mode and signed pending.
        try:
            parsed=read_frame(BytesIO(raw))
        except ValueError:
            return None
        if parsed.get('op') not in (PUBLISH,READ):
            return None
        try:
            if stream.read(1)!=b'':
                raise ValueError('unexpected projected frame trailing data')
            return dispatch(raw,verified_peer_uid=uid,active=protected_marker_active(),
                read_admission=read_owned_record,read_projection=read_projection,
                persist_projection=persist_projection,read_locked_view=read_locked_view)
        except (OSError,ValueError):
            return {'status':'rejected'}
    # Trusted, current root controller's exact protected phase callback.
    # Do not derive phase from an incoming deploy-owned request or journal.
    def pending():
        if protected_marker_active() is not True:
            raise ValueError('terminal publication not enabled')
        current=read_owned_record()
        _,admission,_=read_exact(current)
        if admission is None:
            return None
        return current,pending_request(current,verified_root_phase=independently_observed_phase())
    def finish(*,expected,replacement):
        req=pending_request(expected,verified_root_phase=independently_observed_phase())
        after=commit(expected,proven_root_request=req,terminal_receipt=replacement)
        return publish(expected=expected,replacement=after)
    def ack(*,expected_receipt):
        if protected_marker_active() is not True:
            raise ValueError('terminal publication not enabled')
        current=read_owned_record()
        digest=acknowledge_exact(current,expected_receipt=expected_receipt)
        acknowledge(expected_receipt=current) # re-fsync exact whole protected record
        return digest
    def ordinary_or_pair(channel):
        channel.settimeout(4)
        with channel.makefile('rb') as stream:
            raw=stream.readline(20_001)
            pair_has_extra=raw==REQUEST and bool(stream.read(1))
            response=projection(raw,986,stream)
        if response is not None:
            channel.sendall(json.dumps(response,separators=(',',':')).encode('ascii')+b'\n')
            return
        marker=protected_marker_active()
        if raw==REQUEST:
            if marker is not True or pair_has_extra:
                channel.sendall(b'{"status":"rejected"}\n')
                return
            try:
                response=respond(raw_request=raw,verified_peer_uid=986,
                                 read_authorizer_state=read_owned_record,
                                 independently_observed_phase=independently_observed_phase)
            except (OSError, ValueError):
                response=b'{"status":"rejected"}\n'
            channel.sendall(response)
            return
        try:
            frame=read_frame(BytesIO(raw))
            if marker is True and sealed_terminal_dispatch is not None:
                response=sealed_terminal_dispatch(frame)
                channel.sendall(json.dumps(response,separators=(',',':')).encode('ascii')+b'\n')
                return
            if marker is not False:
                raise ValueError('protected terminal admission unavailable')
        except Exception:
            channel.sendall(b'{"status":"rejected"}\n')
            return
        return normal(channel,prefetched_frame=frame)
    extra={'root_projection':lambda raw,stream:projection(raw,0,stream)} if terminal_projection_enabled else {}
    return dispatch_existing_authorizer(peer,normal=ordinary_or_pair,read_exact_pending=pending,
        finish_cas=finish,acknowledge_terminal=ack,**extra)
