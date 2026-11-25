"""Validate a native receipt from trusted, independently verified provider readbacks."""
import argparse
import io
import tarfile
from pathlib import Path
from package_release import FORMAT,HEX,LANES,PAUSE_FORMAT,PAUSE_MODULES,SHA,TAG,digest,read_doc,valid,wire
import json

def read_bundle(bundle,expected_sha):
    if len(bundle)>150000 or not valid(HEX,expected_sha) or digest(bundle)!=expected_sha:
        raise ValueError('wrong immutable release archive')
    with tarfile.open(fileobj=io.BytesIO(bundle),mode='r:gz') as tar:
        members=tar.getmembers()
        names={'manifest.json','worker.mjs','profiles/test.json','profiles/prod.json'}
        newer=names | {'test/pause/'+name for name in PAUSE_MODULES}
        actual={m.name for m in members}
        if actual not in (names,newer) or len(members)!=len(actual) or any(not m.isfile() or m.size>65536 for m in members):
            raise ValueError('unexpected archive shape')
        files={m.name:tar.extractfile(m).read() for m in members}
    manifest=json.loads(files.pop('manifest.json'))
    if (type(manifest) is not dict or set(manifest)!={'version','tag','source_sha','files'}
            or manifest['version']!=(FORMAT if actual==names else PAUSE_FORMAT)
            or not valid(TAG,manifest['tag']) or not valid(SHA,manifest['source_sha'])
            or manifest['files']!={key:digest(val) for key,val in files.items()}):
        raise ValueError('archive files drift')
    return manifest,files

def validate(bundle,expected_sha,receipt):
    manifest,files=read_bundle(bundle,expected_sha)
    lanes=receipt.get('lane')
    fields={'tag','archive_sha256','lane','published_source_sha','sites_deployment_id','sites_saved_version_id',
            'owner_only_before','owner_only_after','project_id_sha256','access_identity_sha256_before',
            'access_identity_sha256_after','plugin_id_sha256_before','plugin_id_sha256_after','native_status'}
    if (set(receipt)!=fields or lanes not in LANES or receipt['tag']!=manifest['tag']
            or receipt['archive_sha256']!=expected_sha or receipt['native_status']!='succeeded'
            or not valid(SHA,receipt['published_source_sha'])):
        raise ValueError('receipt not bound to release')
    profile=json.loads(files[f'profiles/{lanes}.json'])
    if (receipt['owner_only_before'] is not True or receipt['owner_only_after'] is not True
            or receipt['project_id_sha256']!=profile['project_id_sha256']
            or receipt['access_identity_sha256_before']!=profile['access_identity_sha256']
            or receipt['access_identity_sha256_after']!=profile['access_identity_sha256']
            or not valid(HEX,receipt['plugin_id_sha256_before'])
            or receipt['plugin_id_sha256_before']!=receipt['plugin_id_sha256_after']):
        raise ValueError('Site or owner scope drift')
    for k in ('sites_deployment_id','sites_saved_version_id'):
        if not isinstance(receipt[k],str) or not 0<len(receipt[k])<=150 or any(not x.isascii() or not x.isalnum() and x not in '_-' for x in receipt[k]):
            raise ValueError('native provider identity missing')
    # IDs may remain in the operator's private original receipt; this summary is safe to log.
    return {'tag':manifest['tag'],'archive_sha256':expected_sha,'lane':lanes,
            'published_source_sha':receipt['published_source_sha'],'owner_only':True,
            'sites_deployment_id_sha256':digest(receipt['sites_deployment_id'].encode()),
            'sites_saved_version_id_sha256':digest(receipt['sites_saved_version_id'].encode())}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True)
    p.add_argument('--expected-archive-sha256',required=True)
    p.add_argument('--receipt',type=Path,required=True)
    x=p.parse_args()
    if x.bundle.is_symlink() or not x.bundle.is_file() or x.bundle.stat().st_size>150000:
        raise ValueError('unsafe package')
    print(wire(validate(x.bundle.read_bytes(),x.expected_archive_sha256,read_doc(x.receipt))).decode(),end='')
if __name__=='__main__':main()
