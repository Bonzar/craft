"""Protected Base v3 applicant using exact existing workflow transport.

Not selected by the existing workload. Wire from the independently reviewed
protected profile, not a browser value or fallback after a failed challenge.
"""
import hashlib
import tarfile
from configured_base_prod_gate import intent_document,permit
from configured_terminal_admission import signed_audience
from transition_witness import Release

MARKER='BASE_BROWSER_LISTENER_CONTRACT'
MANIFEST='SHA256SUMS'
EXPECTED=b'assertion-memory-base-browser-listener-v1\n'

def archive_contract(archive, promotion):
    if hashlib.file_digest(archive,'sha256').hexdigest()!=promotion.content.sha256:
        raise ValueError('configured Base archive digest mismatch')
    archive.seek(0)
    state={}
    with tarfile.open(fileobj=archive,mode='r|gz') as pack:
        for offset,member in enumerate(pack):
            if offset>=200_000:
                raise ValueError('oversized configured Base tar index')
            name=member.name.removeprefix('./')
            if name not in (MARKER,MANIFEST):
                continue
            limit=5*1024*1024 if name==MANIFEST else len(EXPECTED)
            if name in state or not member.isfile() or not 0<member.size<=limit:
                raise ValueError('duplicate or untrusted configured Base package contract')
            with pack.extractfile(member) as f:
                raw=f.read(limit+1)
            if len(raw)!=member.size:
                raise ValueError('invalid configured Base package member')
            state[name]=raw
    if MANIFEST not in state:
        raise ValueError('missing configured Base package manifest')
    manifest=state[MANIFEST]
    rows=[line for line in manifest.splitlines() if line.partition(b'  ')[2] in
            (MARKER.encode('ascii'),b'./'+MARKER.encode('ascii'))]
    marker=state.get(MARKER)
    if not ((marker is None and not rows) or (marker==EXPECTED and rows==[
            hashlib.sha256(EXPECTED).hexdigest().encode('ascii')+b'  ./'+MARKER.encode('ascii')])):
        raise ValueError('manifest does not seal the mode marker')
    archive.seek(0)
    return Release(promotion.release.revision,promotion.content.sha256,
            hashlib.sha256(manifest).hexdigest(),'legacy' if marker is None else 'sidecar')

def operate_terminal(exchange,policy,promotion,path,*,get_token,expected_mode):
    if expected_mode not in ('legacy','sidecar'):
        raise ValueError('protected configured Base mode missing')
    with path.open('rb') as archive:
        candidate=archive_contract(archive,promotion)
        if candidate.mode!=expected_mode:
            raise ValueError('unexpected protected qualified archive mode')
        packet=dict(intent=intent_document(promotion),candidate=dict(revision=candidate.revision,
            archive=candidate.archive,manifest=candidate.manifest,mode=candidate.mode))
        cmd='configured-prod-receive'
        response=exchange(cmd,dict(packet,op='terminal-challenge-v3'),None)
        nonce=response.get('nonce')
        if (set(response)!={'status','nonce'} or response['status']!='ok'
                or not isinstance(nonce,str) or len(nonce)!=64
                or any(c not in '0123456789abcdef' for c in nonce)):
            raise ValueError('protected Base challenge denied')
        token=get_token(signed_audience(policy,promotion,nonce,candidate))
        result=exchange(cmd,dict(packet,op='terminal-confirm-v3',nonce=nonce,token=token),archive)
        if result!=permit(promotion):
            raise ValueError('wrong protected Base terminal receipt')
        return result
