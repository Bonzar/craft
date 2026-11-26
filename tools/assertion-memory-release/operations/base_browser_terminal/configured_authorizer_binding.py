"""Internal closures for the existing UID980 configured authorizer.

Not a CLI or network service. Existing configured entrypoint can opt in only
from a reviewed immutable operations bundle, with independently observed
root phase and existing one-use v3 authorization. v1/v2 path unchanged.
"""
from io import BytesIO
from configured_base_prod_gate import read_frame
from guard_lookup import REQUEST, respond
from authorizer_cas import publish,read_owned_record,acknowledge
from configured_terminal_state import (admit,commit,acknowledge_exact,pending_request,read_exact)
from configured_terminal_admission import verify_admission
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

def serve_exact(peer, *, normal, independently_observed_phase, protected_marker_active=lambda:False):
    # Trusted, current root controller's exact protected phase callback.
    # Do not derive phase from an incoming deploy-owned request or journal.
    def pending():
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
        current=read_owned_record()
        digest=acknowledge_exact(current,expected_receipt=expected_receipt)
        acknowledge(expected_receipt=current) # re-fsync exact whole protected record
        return digest
    def ordinary_or_pair(channel):
        channel.settimeout(4)
        with channel.makefile('rb') as stream:
            raw=stream.readline(20_001)
            pair_has_extra=raw==REQUEST and bool(stream.read(1))
        marker=protected_marker_active()
        if raw==REQUEST:
            if marker is not True or pair_has_extra:
                channel.sendall(b'{"status":"rejected"}\n')
                return
            channel.sendall(respond(raw_request=raw,verified_peer_uid=986,
                                   read_authorizer_state=read_owned_record,
                                   independently_observed_phase=independently_observed_phase))
            return
        if marker is not False:
            # Ordinary v1/v2 carriers cannot change active generation once
            # the independently reviewed v3 rollout is in force.
            channel.sendall(b'{"status":"rejected"}\n')
            return
        try:
            frame=read_frame(BytesIO(raw))
        except ValueError:
            channel.sendall(b'{"status":"rejected"}\n')
            return
        return normal(channel,prefetched_frame=frame)
    return dispatch_existing_authorizer(peer,normal=ordinary_or_pair,read_exact_pending=pending,
        finish_cas=finish,acknowledge_terminal=ack)
