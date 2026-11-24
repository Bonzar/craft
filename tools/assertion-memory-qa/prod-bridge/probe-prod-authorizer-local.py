"""Prove local scoped authorizer challenge/denial; never request activation."""
import json
import os
import pwd
import re
import socket
import stat

USER='assertion-production-deploy'
SOCK='/run/assertion-release-authorizer/production.sock'


def probe():
    if os.getuid()==0 or pwd.getpwuid(os.getuid()).pw_name!=USER:
        raise ValueError('wrong local proof identity')
    meta=os.lstat(SOCK)
    if not stat.S_ISSOCK(meta.st_mode) or meta.st_uid!=0 or stat.S_IMODE(meta.st_mode)!=0o660:
        raise ValueError('wrong local proof socket owner/mode')
    i=dict(run_id='900001',test_run_id='900002',test_receipt_id='900003',release_id='900004',
           build_run_id='900005',artifact_id='900006',revision='a'*40,sha256='b'*64)
    def call(r):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
            s.settimeout(4)
            s.connect(SOCK)
            s.sendall(json.dumps(r,separators=(',',':')).encode('ascii')+b'\n')
            with s.makefile('rb') as f:
                data=f.readline(513)
        if not data.endswith(b'\n') or len(data)>512:
            raise ValueError('unexpected authorizer response')
        return json.loads(data)
    got=call({'op':'challenge','intent':i})
    if set(got)!={'status','nonce'} or got['status']!='ok' or not re.fullmatch('[0-9a-f]{64}',got['nonce']):
        raise ValueError('dedicated challenge failed')
    for malformed in ({'op':'challenge','intent':{**i,'revision':'invalid'}},
                      {'op':'confirm','intent':i,'nonce':got['nonce']},
                      {'op':'deploy','intent':i}):
        if call(malformed)!={'status':'rejected'}:
            raise ValueError('scope or incomplete confirmation accepted')
    print('Scoped local challenge passed; three invalid requests denied. No signed proof or activation requested.')


if __name__=='__main__':
    try:
        probe()
    except (OSError,ValueError,TypeError,KeyError):
        raise SystemExit('Local scoped authorizer probe refused') from None
