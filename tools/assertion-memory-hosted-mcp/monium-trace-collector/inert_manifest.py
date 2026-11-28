"""Inert manifest validator. No command runner or authority."""
from hashlib import sha256
import json
MANIFEST_PIN='71b93979cf80ed16b261730891d3365f3f92303d60d3e49357a7a375725c1ab9'
def validate(raw, *, verified_archive, approved_source, verified_assets):
    if type(raw) is not bytes or len(raw)>4096 or sha256(raw).hexdigest()!=MANIFEST_PIN:
        raise ValueError('wrong immutable package manifest')
    job=json.loads(raw)
    target=job['immutable_base']
    if (job['activation'] is not False or job['lane']!='test'
        or job['selector']['unit']!='assertion-monium-traces@test.service'
        or verified_archive!=(target['release_id'],target['archive_sha256'])
        or approved_source!=target['source']):
        raise ValueError('wrong immutable verified release receipt')
    path=job['asset']['path']
    base=path.rsplit('/',1)[0]+'/'
    want={path:(job['asset']['sha256'],job['asset']['bytes']),
          base+'test.yaml':(job['protected_baseline']['test_overlay_sha256'],305),
          base+'prod.yaml':(job['protected_baseline']['prod_overlay_sha256'],305),
          base+'assertion-monium-traces@.service':(job['protected_baseline']['template_unit_sha256'],843)}
    if type(verified_assets) is not dict or set(verified_assets)!=set(want):
        raise ValueError('unsupported asset membership')
    for asset,(digest,length) in want.items():
        b=verified_assets[asset]
        if type(b) is not bytes or len(b)!=length or sha256(b).hexdigest()!=digest:
            raise ValueError('unexpected package identity')
    return (path,job['asset']['sha256'],job['selector']['unit'])
