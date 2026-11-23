"""Offline-testable authorizer service; not installed or used by CI."""
import json
import os
import socket
import sqlite3
import stat
import struct
import sys
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from reusable_proof import Intent, ISSUER, OneUse, Policy, obj, require

JWKS_URL = ISSUER + '/.well-known/jwks'
MAX_REQUEST = 20_000
MAX_KEYS = 64_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Unexpected issuer redirect')


def issuer_keys():
    opener=build_opener(ProxyHandler({}),NoRedirect)
    with opener.open(Request(JWKS_URL,headers={'Accept':'application/json'}),timeout=4) as reply:
        require(reply.status==200 and reply.geturl()==JWKS_URL, 'Issuer response mismatch')
        data=reply.read(MAX_KEYS+1)
    require(len(data)<=MAX_KEYS,'Issuer response too large')
    keys=obj(data)
    require(type(keys.get('keys')) is list and 0 < len(keys['keys']) <= 32,'Invalid issuer key set')
    return keys


def unix_peer_uid(sock):
    return struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))[1]


def request_from(sock):
    sock.settimeout(4)
    with sock.makefile('rb') as stream:
        wire=stream.readline(MAX_REQUEST+1)
    require(0<len(wire)<=MAX_REQUEST and wire.endswith(b'\n'),'Invalid request frame')
    return obj(wire)


def authorize_request(sock, policy, ledger, deploy_uid, *, keys=issuer_keys, now=lambda: int(time.time())):
    try:
        require(unix_peer_uid(sock)==deploy_uid,'Wrong socket peer')
        req=request_from(sock)
        op=req.get('op')
        if op=='challenge':
            require(req.keys()=={'op','intent'},'Invalid challenge fields')
            challenge=ledger.issue(policy,Intent(**req['intent']),now())
            result={'status':'ok','nonce':challenge}
        elif op=='confirm':
            require(req.keys()=={'op','intent','nonce','token'},'Invalid confirm fields')
            permit=ledger.verify_and_consume(req['token'],keys(),policy,Intent(**req['intent']),req['nonce'],now())
            result={'status':'ok',**permit}
        else:
            raise ValueError('Invalid operation')
    except Exception:
        # No caller strings, signed tokens, claims or issuer errors in service diagnostics.
        result={'status':'rejected'}
    sock.sendall(json.dumps(result,separators=(',',':')).encode('ascii')+b'\n')


def read_config(name):
    with os.fdopen(os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as file:
        entry=os.fstat(file.fileno())
        require(stat.S_ISREG(entry.st_mode) and entry.st_uid==0 and entry.st_mode&0o022==0,'Untrusted service config')
        data=file.read(4097)
    require(len(data)<=4096,'Service config too large')
    return obj(data)


def main():
    require(len(sys.argv)==2 and os.getuid()!=0,'Expected dedicated authorizer system user')
    # Production deployment supplies the root-owned configuration path in a fixed systemd unit.
    config=read_config(sys.argv[1])
    require(config.keys()=={'deploy_uid','policy'},'Expected fixed authorizer settings')
    deploy_uid=config['deploy_uid']
    require(type(deploy_uid) is int and deploy_uid>0 and deploy_uid!=os.getuid(),'Invalid deploy UID')
    policy_body=config['policy']
    operators=policy_body['actor_ids']
    require(type(operators) is list and len(set(operators))==len(operators),'Invalid production operator list')
    policy=Policy(**{**policy_body,'actor_ids':frozenset(operators)})
    require(os.environ.get('LISTEN_PID')==str(os.getpid()) and os.environ.get('LISTEN_FDS')=='1','Expected one systemd-owned socket')
    with socket.socket(fileno=3) as sock:
        require(sock.family==socket.AF_UNIX and sock.type==socket.SOCK_STREAM,'Expected local activation socket')
        with sqlite3.connect('/var/lib/assertion-release-auth/nonce.sqlite3',timeout=2) as db:
            ledger=OneUse(db)
            while True:
                channel,_=sock.accept()
                with channel:
                    try:
                        authorize_request(channel,policy,ledger,deploy_uid)
                    except OSError:
                        pass  # A disconnected local client must not crash the authorizer.


if __name__=='__main__':
    main()
