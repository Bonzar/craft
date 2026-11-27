"""Verify exact raw public OCI material staged by an authorized read-only acquirer.

This module only validates data bytes. It cannot extract layers, start Docker,
grant any authority, or declare the historical build reproducible.
"""
from hashlib import file_digest
import json
from pathlib import Path
import re
import os
import stat
import sys

PIN = Path(__file__).with_name('base-035-oci-root-pin.json')
MAX_LAYER = 128 * 1024**2
MAX_METADATA = 4 * 1024**2
SHA = re.compile(r'sha256:([0-9a-f]{64})\Z')


def unique(rows):
    result={}
    for key,val in rows:
        if key in result:
            raise ValueError('Duplicate OCI metadata field')
        result[key]=val
    return result


def verify_blob(directory, digest, *, size=None, metadata=False):
    match=SHA.fullmatch(digest)
    if not match:
        raise ValueError('Unexpected OCI digest')
    fd=os.open(directory/match[1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode)
                or not 0 < info.st_size <= (MAX_METADATA if metadata else MAX_LAYER)
                or (size is not None and (type(size) is not int or info.st_size != size))):
            raise ValueError('OCI descriptor length mismatch')
        if file_digest(source,'sha256').hexdigest()!=match[1]:
            raise ValueError('Unverified OCI raw bytes')
        if metadata:
            source.seek(0)
            result=json.loads(source.read(MAX_METADATA+1),object_pairs_hook=unique)
            if type(result) is not dict:
                raise ValueError('Invalid OCI metadata shape')
            return result


def verify(directory, pin):
    index=verify_blob(directory,pin['index'],metadata=True)
    if index.get('schemaVersion')!=2 or index.get('mediaType')!='application/vnd.oci.image.index.v1+json':
        raise ValueError('Unexpected OCI index format')
    named=[m for m in index['manifests'] if all(m.get('platform',{}).get(k)==v
                                             for k,v in pin['platform'].items())]
    if len(named)!=1 or named[0].get('digest')!=pin['manifest']:
        raise ValueError('OCI index not pinned to exact platform')
    image=verify_blob(directory,pin['manifest'],size=named[0]['size'],metadata=True)
    if (image.get('schemaVersion')!=2 or image['config'].get('digest')!=pin['config']
            or [l.get('digest') for l in image['layers']]!=pin['layers']):
        raise ValueError('OCI manifest does not bind exact layers/config')
    config=verify_blob(directory,pin['config'],size=image['config']['size'],metadata=True)
    if any(config.get(k)!=v for k,v in pin['platform'].items()):
        raise ValueError('OCI configuration platform mismatch')
    for layer in image['layers']:
        verify_blob(directory,layer['digest'],size=layer['size'])
    return {'index':pin['index'],'manifest':pin['manifest'],'platform':pin['platform'],
            'verified_data_only':True,'build_approved':False}


if __name__=='__main__':
    print(json.dumps(verify(Path(sys.argv[1]),json.loads(PIN.read_bytes(),object_pairs_hook=unique)),
                     sort_keys=True))
