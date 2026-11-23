"""Strict host-side bridge API; this demo is not connected to active SSH."""
from dataclasses import asdict
import json
from pathlib import Path
import re
import socket
import stat

from authorizer import MAX_REQUEST, unix_peer_uid
from reusable_proof import Intent, obj, require

PROD_SOCKET = Path('/run/assertion-release-authorizer/production.sock')
DEPLOY = re.compile(r'deploy-release production ([0-9a-f]{40}) ([0-9a-f]{64})')


def exchange(payload, *, socket_path=PROD_SOCKET):
    # Both root-owned socket inode and authenticated systemd listener are required.
    parent=socket_path.parent.lstat()
    entry=socket_path.lstat()
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid==0 and parent.st_mode&0o022==0
            and stat.S_ISSOCK(entry.st_mode) and entry.st_uid==0 and entry.st_mode&0o007==0,
            'Unsafe authorizer socket')
    wire=json.dumps(payload,separators=(',',':')).encode('ascii')+b'\n'
    require(len(wire)<=MAX_REQUEST,'Authorization request too large')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
        conn.settimeout(5)
        conn.connect(str(socket_path))
        require(unix_peer_uid(conn)==0,'Not the trusted systemd socket')
        conn.sendall(wire)
        with conn.makefile('rb') as response:
            reply=response.readline(512)
    require(reply.endswith(b'\n'),'Invalid authorizer reply')
    return obj(reply)


def authorization_frame(stream):
    # One bounded JSON line, then raw archive. Never put the JWT on argv.
    frame=stream.readline(MAX_REQUEST+1)
    require(len(frame)<=MAX_REQUEST and frame.endswith(b'\n'),'Invalid authorization envelope')
    return obj(frame)


def handle(command, stream, deploy_verified, *, authorize=exchange):
    """Validate authorization before invoking code touching production releases.

    The injected deploy_verified is the trusted receiver's compiled-in action;
    it must independently stream/hash bytes before stage/prepare/activate.
    Never use client-supplied code, paths or claims to choose this callback.
    """
    if command=='authorize-release production':
        message=authorization_frame(stream)
        require(message.keys()=={'intent'} and stream.read(1)==b'', 'Invalid challenge request')
        intent=Intent(**message['intent'])
        value=authorize({'op':'challenge','intent':asdict(intent)})
        require(value.keys()=={'status','nonce'} and value['status']=='ok'
                and re.fullmatch(r'[0-9a-f]{64}',value['nonce']),'Authorizer denied challenge')
        return value
    match=DEPLOY.fullmatch(command)
    require(match is not None,'command_denied')
    message=authorization_frame(stream)
    require(message.keys()=={'intent','nonce','token'},'Invalid deploy envelope')
    intent=Intent(**message['intent'])
    require((intent.revision,intent.sha256)==match.groups(),'Intent and authorized command disagree')
    permit=authorize({'op':'confirm',**message})
    require(permit=={'status':'ok','environment':'production','revision':intent.revision,'sha256':intent.sha256},
            'Authorizer denied release')
    return deploy_verified(intent.revision,intent.sha256,stream)
