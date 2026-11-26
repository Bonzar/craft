"""TEST-only deterministic rollout receipt, not an installer or a database writer."""
from hashlib import sha256
import json
import re

TEST_MARKER='/etc/assertion-release/test/browser-owner-clear-profile.json'
TEST_DB='/srv/assertion-release/test/state/fictional.sqlite3'
TEST_UNIT='assertion-test-deploy-graph.service'
TEST_BASE='/srv/assertion-release/test/releases'
TEST_ORIGIN='https://d5dc14bhh6hba54q0i3p.hsvi2zuh.apigw.yandexcloud.net'
# Exact provider paths are verified by the authenticated normal Visual owner smoke.
PREPARE='/api/v1/processing/database/clear-v2/prepare'
COMMIT='/api/v1/processing/database/clear-v2'
HEX64=re.compile(r'[0-9a-f]{64}\Z')
HEX40=re.compile(r'[0-9a-f]{40}\Z')
TAG=re.compile(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+\Z')

def compact(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')

def pin(value,pattern):
    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError('Missing independently verified TEST release pin')
    return value

def release_generation(revision,archive,tag,stage):
    e={'revision':revision,'archive':archive,'tag':tag,'lane':stage}
    return sha256(compact(e)).hexdigest()

def plan(evidence, *, revision, archive, tag, expected_evidence_sha256, expected_manifest_sha256, database_scope=None):
    pin(expected_evidence_sha256,HEX64)
    pin(expected_manifest_sha256,HEX64)
    pin(revision,HEX40); pin(archive,HEX64); pin(tag,TAG)
    if type(evidence) is not dict or sha256(compact(evidence)).hexdigest()!=expected_evidence_sha256:
        raise ValueError('Live TEST manifest/readiness evidence drift')
    generation=release_generation(revision,archive,tag,'test')
    expected={
      'unit':TEST_UNIT,'account':'assertion-test-deploy','stage':'test','database':TEST_DB,'origin':TEST_ORIGIN,
      'current':TEST_BASE+'/'+revision+'-'+archive,'loaded_cwd':TEST_BASE+'/'+revision+'-'+archive,
      'release_generation':generation,'release_version':tag,'activated_same_origin':True,
      'signed_members_verified':True,'marker_absent':True,'journal_absent':True,'old_alias_denied':True,
      'graph_usable':True,'base_mcp_healthy':True,'profile_code_present':True,
      'manifest_sha256':expected_manifest_sha256,
    }
    if set(evidence)!=set(expected)|{'uid','effective_uid','configured_uid','database_scope'}:
        raise ValueError('Unexpected TEST evidence shape')
    for name,value in expected.items():
        if type(evidence[name]) is not type(value) or evidence[name]!=value:
            raise ValueError('Wrong canonical TEST authority: '+name)
    uid=evidence['configured_uid']
    if type(uid) is not int or uid<=0 or any(type(evidence[k]) is not int or evidence[k]!=uid for k in ('uid','effective_uid')):
        raise ValueError('Wrong TEST service account')
    pin(evidence['database_scope'],HEX64)
    if database_scope is not None and pin(database_scope,HEX64)!=evidence['database_scope']:
        raise ValueError('Requested one-shot scope is not exact current TEST database epoch')
    profile=compact({'version':1,'stage':'test','release_generation':generation,
                     'database_scope':database_scope})+b'\n'
    return {'stage':'test','unit':TEST_UNIT,'origin':TEST_ORIGIN,
            'marker':TEST_MARKER,'mode':0o644,'owner':0,
            'marker_sha256':sha256(profile).hexdigest(),'profile':profile,
            'revision':revision,'archive':archive,'release_generation':generation,'manifest_sha256':expected_manifest_sha256,
            'pretest_database_scope':evidence['database_scope'],
            'reusable':database_scope is None,
            'writes_performed':False,'deletion_performed':False}
