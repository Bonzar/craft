/Users/bonzar
"""Render only private files for review; never installs or reloads services."""
import base64
import json
import os
from pathlib import Path
import pwd
import re
import stat
import struct
import sys

PIN='a3afb72811483ddef20eb6371213e64bf9505424'
ID=r'[1-9][0-9]{0,19}'
USER='assertion-production-deploy'
ENV='assertion-memory-production'
PUSHER_POLICY='assertion-release-auth'
FRAGMENT='''Match User assertion-production-deploy
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthorizedKeysFile /etc/ssh/assertion-release/production.authorized_keys
    ForceCommand /usr/bin/python3 -I -B /etc/assertion-release/production/receive-authorized-entry.py production
    DisableForwarding yes
    PermitTTY no
    PermitUserRC no
Match all
'''


def require(ok,msg):
    if not ok:
        raise ValueError(msg)


def validate_input(info, pub, deploy_uid, authorizer_uid):
    require(isinstance(info,dict) and set(info)=={'vm_id','subject','environment_node_id','actor_id'},
            'Unexpected trust data fields')
    require(deploy_uid>0 and authorizer_uid>0 and deploy_uid!=authorizer_uid,
            'Invalid scoped principals')
    checks={'vm_id':r'[A-Za-z0-9.-]{1,128}','environment_node_id':r'[A-Za-z0-9_=-]{4,180}',
            'actor_id':ID,'subject':r'repo:Bonzar/craft:environment:'+ENV}
    require(all(isinstance(info[k],str) and re.fullmatch(v,info[k]) for k,v in checks.items()),
            'One or more witnessed policy identities invalid')
    require(isinstance(pub,str) and chr(0) not in pub and len(pub)<8192 and len(pub.splitlines())==1,
            'Expected exactly one public key')
    kind,wire,*comment=pub.split()
    require(kind in ('ssh-ed25519','ssh-rsa'),'Unsupported public key')
    blob=base64.b64decode(wire,validate=True)
    def field():
        nonlocal blob
        require(len(blob)>=4,'Truncated public key')
        n=struct.unpack('>I',blob[:4])[0]
        require(0<n<=len(blob)-4,'Malformed key length')
        out,blob=blob[4:4+n],blob[4+n:]
        return out
    require(field()==kind.encode('ascii'),'Public key type mismatch')
    if kind=='ssh-ed25519':
        require(len(field())==32,'Malformed Ed25519 key')
    else:
        e,n=(int.from_bytes(field(),'big') for _ in range(2))
        require(e==65537 and 3072<=n.bit_length()<=4096,'RSA key size or exponent outside policy')
    require(not blob,'Unrecognized public key fields')
    policy=dict(deploy_uid=deploy_uid,policy=dict(repository_id='1260097465',reusable_sha=PIN,
                 vm_id=info['vm_id'],subject=info['subject'],environment_node_id=info['environment_node_id'],
                 actor_ids=[info['actor_id']]))
    return policy,'restrict '+kind+' '+wire+'\n'


def read_root_file(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        m=os.fstat(f.fileno())
        require(m.st_uid==0 and stat.S_ISREG(m.st_mode) and not(m.st_mode&0o077),
                'Trust input is not a private root-owned file')
        d=f.read(8193)
    require(len(d)<=8192,'Trust input too large')
    return d


def main(args):
    require(os.geteuid()==0 and len(args)==1,'Run in an authorized root shell for local review only')
    out=Path(args[0])
    require(out.is_absolute() and out.resolve()==out and out.parent==Path('/root'),
            'Stage directory must be a canonical direct child of /root')
    m=out.lstat()
    require(m.st_uid==0 and stat.S_ISDIR(m.st_mode) and not(m.st_mode&0o077),
            'Stage directory must be private root-owned')
    p,k=validate_input(json.loads(read_root_file(out/'witness-input.json')),
                      read_root_file(out/'deployment-key.pub').decode(),
                      pwd.getpwnam(USER).pw_uid,pwd.getpwnam(PUSHER_POLICY).pw_uid)
    names={'authorization-policy.candidate.json':json.dumps(p,sort_keys=True)+'\n',
           'production.authorized_keys.candidate':k,'sshd-dropin.candidate':FRAGMENT}
    require(all(not (out/n).exists() and not (out/n).is_symlink() for n in names),
            'Review output already exists')
    os.umask(0o077)
    for name,value in names.items():
        with (out/name).open('x',encoding='ascii') as f: f.write(value)
    print('Rendered three private stage-two review files; no live services, key or settings changed.')


if __name__=='__main__':
    try:
        main(sys.argv[1:])
    except (ValueError, OSError, TypeError, KeyError):
        raise SystemExit('Base PROD authorization render refused; no live change') from None
