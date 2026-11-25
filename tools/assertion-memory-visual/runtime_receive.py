"""Experimental bound TEST Visual receiver envelope. No privileged operations."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

if __package__:
    from .runtime_companion import MAX_ZIP, identity, verify_signed, wire
else:
    from runtime_companion import MAX_ZIP, identity, verify_signed, wire

COMMAND=re.compile(r'deploy-visual-runtime test ([a-f0-9]{40})')
MAX_STATIC=5_000_000


def read_exact(source,size):
    data=bytearray()
    while len(data)<size:
        new=source.read(size-len(data))
        if not new:
            raise ValueError('Truncated authenticated Visual frame')
        data.extend(new)
    return bytes(data)


def length(source, maximum, size=10):
    raw=source.readline(size)
    if not re.fullmatch(rb'[1-9][0-9]{0,8}\n',raw) or int(raw)>maximum:
        raise ValueError('Wrong Visual frame length')
    return int(raw)


def receive(stage, command, source, *, signers, private_workspace, activate):
    """Validate both payloads completely, then call the preinstalled scoped transaction."""
    m=COMMAND.fullmatch(command)
    if stage!='test' or not m or not callable(activate):
        raise ValueError('TEST Visual joint receiver not provisioned')
    signed_raw=read_exact(source,length(source,4096))
    proof=read_exact(source,length(source,4096))
    try:
        fields=json.loads(signed_raw)
        expected=identity(**fields)
    except (TypeError, ValueError, UnicodeError) as e:
        raise ValueError('Invalid signed Visual descriptor') from e
    rev=m[1]
    static_sha=expected['static_sha']
    if wire(expected)!=signed_raw or expected['revision']!=rev:
        raise ValueError('Visual command is not the signed release')
    runtime=read_exact(source,length(source,MAX_ZIP))
    runtime_sha=sha256(runtime).hexdigest()
    files=verify_signed(runtime,expected,runtime_sha,proof,signers)
    n=length(source,MAX_STATIC)
    with TemporaryDirectory(dir=private_workspace,prefix='.joint-visual-') as d:
        archive=Path(d)/'visual.tar'
        digest=sha256()
        with archive.open('xb') as out:
            while n:
                data=read_exact(source,min(n,64*1024))
                out.write(data)
                digest.update(data)
                n-=len(data)
        if source.read(1) or digest.hexdigest()!=static_sha:
            raise ValueError('Unverified Visual static payload')
        return activate(expected,archive,runtime_sha,files)


def assemble(expected, runtime_raw, signature, static_raw):
    """Runner framing; its transport can only select the exact TEST receiver command."""
    revision=expected['revision']
    static_sha=sha256(static_raw).hexdigest()
    command=f'deploy-visual-runtime test {revision}'
    if static_sha!=expected['static_sha'] or not COMMAND.fullmatch(command):
        raise ValueError('Mismatched Visual sender identity')
    chunks=(wire(expected),signature,runtime_raw,static_raw)
    limits=(4096,4096,MAX_ZIP,MAX_STATIC)
    if any(type(c) is not bytes or not 0<len(c)<=n for c,n in zip(chunks,limits)):
        raise ValueError('Invalid bounded Visual frame')
    return command,BytesIO(b''.join(str(len(c)).encode()+b'\n'+c for c in chunks))
