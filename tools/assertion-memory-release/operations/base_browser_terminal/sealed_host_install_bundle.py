"""Closed install manifest for reviewed Base operations source, no host I/O."""
import json
from hashlib import sha256
import re

OPS='tools/assertion-memory-release/operations/base_browser_terminal/'
CORE='tools/assertion-memory-release/'
QA='tools/assertion-memory-qa/'
INSTALL='/etc/assertion-release/configured-base-prod/'
PROD='/etc/assertion-release/production/'
MOUNT=INSTALL+'base-terminal-binding/'
STAGED={
    '/usr/local/sbin/assertion-base-prod-transition':(0o755,'stock-compiled-guard'),
    PROD+'prod_service_hook.py':(0o644,'stock-compiled-hook'),
    INSTALL+'authorizer-entry.py':(0o644,QA+'configured-base-prod/authorizer-entry.py'),
    INSTALL+'receiver-entry.py':(0o755,QA+'configured-base-prod/receiver-entry.py'),
    INSTALL+'configured_base_prod_gate.py':(0o644,QA+'configured_base_prod_gate.py'),
}
BOUND=(
    'authorizer_cas','configured_authorizer_binding','configured_entry_binding',
    'configured_terminal_admission','configured_terminal_state','controller_install_lease',
    'foreground_terminal','guard_lookup','host_hook_bindings','kernel_observer',
    'native_boundary','native_checkpoint','operator_foreground_controller',
    'operator_foreground_main','operator_foreground_phase','ops_readonly_preflight',
    'protected_native_activation','root_phase_binding','root_run_records',
    'root_terminal_publisher','settled_guard_binding','terminal_lifecycle',
    'terminal_verifier','transition_witness',
)
MOUNTED={MOUNT+name+'.py':(0o644,OPS+name+'.py') for name in BOUND}
MOUNTED.update({MOUNT+name+'.py':(0o644,CORE+name+'.py') for name in
                ('base_payload','release_core','release_retention')})
OUTPUT_PINS={
    'stock-compiled-guard':'f4f0f349c8b95b80cd087a766b6cd0df1ae0f874581d830a1f9b352740ebcad9',
    'stock-compiled-hook':'8bb05d627bc99b8bc3c83cb4eeaa166b47f9a4483b9da1cf8f4f4d3b18260a6a',
}
STOCK_PINS={
    '/usr/local/sbin/assertion-base-prod-transition':'1b09e5d77c338a148a9df471e0ed010c82f104a2f310a0e45b73cd31f67c6c34',
    PROD+'prod_service_hook.py':'0b3f585ffc1668fe3627a967de66d21fb275271c1e891915466542b9a1445c5e',
    INSTALL+'authorizer-entry.py':'4fb7e3809728a4ac4d5cf70f9c61b240117a7fc1b91dafbc9ddb952dc4f781c7',
    INSTALL+'receiver-entry.py':'21b9b97ffacda9c1ef220ca4acd7d7f116aedffeb9fb270db01dd218f25c4872',
    INSTALL+'configured_base_prod_gate.py':'fa969a0424c9257ef09c9ec4f2c6bc8bb781010c1278b712b08552569af54ca6',
}

def make_manifest(*, source_commit, exact_source_digest_map):
    if type(source_commit) is not str or re.fullmatch('[0-9a-f]{40}',source_commit) is None:
        raise ValueError('immutable approved operations source required')
    inputs=STAGED|MOUNTED
    expected={k for mode,k in inputs.values()}-OUTPUT_PINS.keys()
    if (type(exact_source_digest_map) is not dict or set(exact_source_digest_map)!=expected or
            any(type(x) is not str or re.fullmatch('[0-9a-f]{64}',x) is None
                for x in exact_source_digest_map.values())):
        raise ValueError('closed immutable package source pins required')
    after=exact_source_digest_map|OUTPUT_PINS
    result={'schema':1,'source':source_commit,'marker':'must-remain-dark','files':[]}
    for path,(mode,source) in sorted(inputs.items()):
        result['files'].append(dict(destination=path,mode=mode,source=source,
                    before=STOCK_PINS.get(path),after=after[source]))
    return json.dumps(result,sort_keys=True,separators=(',',':')).encode('ascii')

def verify_source_set(verified_manifest, *, source_commit, exact_source_digest_map, candidate_images):
    exact=make_manifest(source_commit=source_commit,exact_source_digest_map=exact_source_digest_map)
    if verified_manifest!=exact:
        raise ValueError('protected immutable installer manifest mismatch')
    rows=json.loads(exact)['files']
    if type(candidate_images) is not dict or set(candidate_images)!={x['source'] for x in rows}:
        raise ValueError('unexpected package namespace')
    for row in rows:
        data=candidate_images[row['source']]
        if (type(data) is not bytes or not 0<len(data)<=1024*1024 or
                sha256(data).hexdigest()!=row['after']):
            raise ValueError('immutable operations source digest mismatch')
        compile(data,row['source'],'exec',dont_inherit=True)
    return tuple(rows)
