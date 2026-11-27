"""Offline archive/project provenance checks for the inert manifest."""
from pathlib import Path
from unittest import TestCase
from inert_manifest import validate

BASE=Path(__file__).resolve().parent
REL='tools/assertion-memory-hosted-mcp/monium-trace-collector/'
REV='1c022bfdd3d728d94cb5ea77a68970ca4d7348e2'
ARCHIVE='dabbaa2280ea83af62f2c272eec7d17820e380379c0d72b53fa0fd70cb34a3a1'

def test_closed_inert_manifest():
    t=TestCase()
    raw=(BASE/'test-inert-manifest.json').read_bytes()
    assets={REL+n:(BASE/n).read_bytes() for n in (
        'common.yaml','test.yaml','prod.yaml','assertion-monium-traces@.service')}
    d=dict(verified_archive=(460246427,ARCHIVE),approved_source=REV,verified_assets=assets)
    path,digest,unit=validate(raw,**d)
    t.assertEqual((path,unit),(REL+'common.yaml','assertion-monium-traces@test.service'))
    t.assertEqual(digest,'bba5c6e6a4f7f1b61870bb50db0cc9d239ee66dcaa9d93a97255219c557a1b25')
    t.assertRaises(ValueError,validate,raw.replace(b'false',b'true'),**d)
    t.assertRaises(ValueError,validate,raw.replace(b'"test"',b'"prod"'),**d)
    for k,v in [('verified_archive',(460246427,'e'*64)),('approved_source','2'*40),
                ('verified_assets',assets|{'secret.env':b''}),
                ('verified_assets',assets|{path:b'changed'})]:
        t.assertRaises(ValueError,validate,raw,**(d|{k:v}))

if __name__=='__main__':
    test_closed_inert_manifest()
    print('inert manifest fixture PASS')
