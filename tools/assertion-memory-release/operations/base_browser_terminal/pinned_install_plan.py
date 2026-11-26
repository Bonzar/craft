"""Review-only immutable plan: consumes metadata and candidate source bytes.

No command execution, host filesystem access, credentials or installation.
Input constants must be complete and independently read by authorized operator.
"""
from hashlib import sha256
import json
import re

GUARD='/usr/local/sbin/assertion-base-prod-transition'
HOOK='/etc/assertion-release/production/prod_service_hook.py'
SHELL='/etc/assertion-release/production/service-hook.sh'
AUTH_ENTRY='/etc/assertion-release/configured-base-prod/authorizer-entry.py'
KNOWN={
 GUARD:'1b09e5d77c338a148a9df471e0ed010c82f104a2f310a0e45b73cd31f67c6c34',
 HOOK:'0b3f585ffc1668fe3627a967de66d21fb275271c1e891915466542b9a1445c5e',
 SHELL:'9801563b96c7e6d3702f5a9d2635691f9c33c5361fff1907fff91efa823aa533',
}
_ALLOWED_ROLES=frozenset(('replace','verify-only'))
_ALLOWED_PATHS=frozenset((GUARD,HOOK,SHELL,AUTH_ENTRY))
_DIGEST=re.compile(r'[a-f0-9]{64}\Z')

def plan(*, intended, observed, new_bytes, release, inventory):
    """An allowlisted exact set plus protected metadata, not a live wrapper."""
    if not _DIGEST.fullmatch(release) or not intended or set(observed)!=set(intended):
        raise ValueError('expected complete exact approved inventory')
    if set(intended)-_ALLOWED_PATHS or set(intended)-set(inventory) or set(new_bytes)!={p for p,r in intended.items() if r=='replace'}:
        raise ValueError('unexpected source or path')
    # The versioned transition policy, host trust and auth profile are NOT
    # modified by this plan; only approved root-managed source files.
    entries=[]
    for path,role in sorted(intended.items()):
        if role not in _ALLOWED_ROLES:
            raise ValueError('unreviewed new protected host paths')
        expected=inventory[path]
        value=observed[path]
        if (type(expected) is not dict or set(expected)!={'sha256','owner','group','mode','links','kind'}
                or expected!=value or value['owner']!=0 or value['group']!=0
                or value['links']!=1 or value['kind']!='regular'
                or value['mode'] not in (0o444,0o555,0o644,0o755)
                or not _DIGEST.fullmatch(value['sha256'])
                or path in KNOWN and value['sha256']!=KNOWN[path]):
            raise ValueError('unmatched baseline path metadata')
        if role=='replace' and type(new_bytes[path]) is not bytes:
            raise ValueError('expected exact reviewed source bytes')
        updated=sha256(new_bytes[path]).hexdigest() if role=='replace' else value['sha256']
        if role=='replace' and updated==value['sha256']:
            raise ValueError('replacement is unchanged')
        entries.append(dict(path=path,role=role,rollback=value['sha256'],candidate=updated,
                            mode=value['mode'],owner=value['owner'],group=value['group']))
    wire={'schema':1,'base_release_archive':release,'effect':'review-only',
          'entries':entries,'new_roles':False,'new_credentials':False,
          'data_mutation':False}
    return json.dumps(wire,sort_keys=True,separators=(',',':')).encode('ascii')+b'\n'
