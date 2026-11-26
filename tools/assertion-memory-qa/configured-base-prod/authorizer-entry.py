#!/usr/bin/python3 -I
"""A separate unprivileged, systemd socket-activated PROD authorizer."""
import os
from pathlib import Path
import socket
import sqlite3
import sys
import time
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE/'code/tools/assertion-memory-qa')]
sys.path.insert(0,'/etc/assertion-release/production/authorization')
from authorizer import issuer_keys, read_config, unix_peer_uid
from configured_base_prod_gate import Ledger, Policy, serve_peer
from reusable_proof import require


# Read-only, exact, root-installed ops directory. Source-only builds leave it absent.
PROTECTED=Path('/etc/assertion-release/configured-base-prod/base-terminal-binding')

def protected_binding():
    import stat
    marker=Path('/etc/assertion-release/production/base-browser-listener.json')
    if not (marker.exists() or marker.is_symlink()):
        return None
    require(PROTECTED.resolve()==PROTECTED,'Wrong protected binding path')
    for directory in (PROTECTED,*PROTECTED.parents):
        m=directory.lstat()
        require(stat.S_ISDIR(m.st_mode) and m.st_uid==0 and m.st_gid==0 and not m.st_mode&0o022,
                'Unprotected terminal binding directory')
    sys.path.insert(0,str(PROTECTED))
    import configured_entry_binding
    return configured_entry_binding

def main():
    require(len(sys.argv)==1 and os.getuid()==os.geteuid()!=0
            and os.environ.get('LISTEN_PID')==str(os.getpid()) and os.environ.get('LISTEN_FDS')=='1',
            'Wrong systemd activation')
    cfg=read_config(HERE/'policy.json')
    require(set(cfg)=={'writer_uid','policy'} and type(cfg['writer_uid']) is int
            and cfg['writer_uid']>0 and cfg['writer_uid']!=os.getuid(),'Invalid writer policy')
    raw=cfg['policy']
    require(type(raw['actor_ids']) is list and len(raw['actor_ids'])==len(set(raw['actor_ids'])),'Wrong actor list')
    policy=Policy(**(raw|{'actor_ids':frozenset(raw['actor_ids'])}))
    with socket.socket(fileno=3) as srv:
        require(srv.family==socket.AF_UNIX and srv.type==socket.SOCK_STREAM,'Not scoped unix socket')
        binding=protected_binding()
        with sqlite3.connect('/var/lib/assertion-configured-base-prod-auth/nonce.sqlite3',timeout=2) as db:
            ledger=Ledger(db)
            while True:
                channel,_=srv.accept()
                with channel:
                    try:
                        if binding is None:
                            serve_peer(channel,policy,ledger,cfg['writer_uid'],unix_peer_uid,issuer_keys)
                        else:
                            binding.authorize(channel,policy=policy,ledger=ledger,writer_uid=cfg['writer_uid'],
                                peer_uid=unix_peer_uid,keys=issuer_keys,now=lambda:int(time.time()))
                    except OSError:pass
if __name__=='__main__':main()
