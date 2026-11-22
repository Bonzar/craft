"""Offline provider/queue regression: no account, network, or real database."""
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.model import (
    CodexModel, ModelError, ModelProviderUnavailable, ModelRequest, persisted_model_error_code,
)
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_processing import TracedThoughtWorker

PRIVATE_DIAGNOSTIC = "SYNTHETIC-PRIVATE-STDERR-MARKER"
FAKE_CODEX = '''#!/usr/bin/env python3
import json, os, pathlib, stat, sys
a=sys.argv[1:]
assert a[0]=='exec' and a[-1]=='-'
assert a[a.index('--sandbox')+1]=='read-only'
assert all(x in a for x in ('--ignore-user-config','--ephemeral','--json','--skip-git-repo-check'))
disabled=[a[i+1] for i,x in enumerate(a[:-1]) if x=='--disable']
assert set(disabled)=={'hooks','shell_tool','plugins','apps'}
scratch=pathlib.Path(a[a.index('-C')+1])
assert scratch.is_dir() and stat.S_IMODE(scratch.stat().st_mode)==0o700
assert scratch.parent==pathlib.Path(os.environ['TMPDIR'])
schema=pathlib.Path(a[a.index('--output-schema')+1])
assert json.loads(schema.read_text())['type']=='object'
r=json.loads(sys.stdin.read().split(chr(10)*2,1)[1])
mode=os.environ['FAKE_CODEX_MODE']
if mode=='offline':
    print(json.dumps({'type':'turn.failed','error':{'code':'unsupported_country_region_territory','message':'private-provider-body'}}))
    print('SYNTHETIC-PRIVATE-STDERR-MARKER',file=sys.stderr)
    raise SystemExit(1)
if mode=='auth':
    print('Not logged in. Private authentication context',file=sys.stderr)
    raise SystemExit(1)
if mode=='transport':
    print('Error: stream disconnected before completion: confidential URL',file=sys.stderr)
    raise SystemExit(1)
if mode=='validation':
    print(json.dumps({'type':'turn.failed','error':{'code':'invalid_json_schema','message':'private-provider-body'}}))
    raise SystemExit(2)
if mode=='bad-json':
    print('this is not JSONL')
    raise SystemExit(0)
if mode=='recover-stream':
    print(json.dumps({'type':'error','message':'stream disconnected before completion'}))
if r['task']=='Construct thought':
    source=next(i for i in r['records'] if i['role']=='source')
    fragment=source['fragments'][0]
    ordinary={'thoughts':[{'text':fragment['text'],'question':None,'support_fragments':[fragment['option']]}],'needs_detail':None}
elif r['task']=='Verify thought':
    ordinary={'verdict':'accept','text':'Matches the source.'}
else:
    ordinary={'result':'fictional-success'}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(ordinary)}}))
print(json.dumps({'type':'turn.completed','usage':{}}))
'''

class CodexProviderAvailabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(TemporaryDirectory())).resolve()
        mock=self.tmp/'codex'
        mock.write_text(FAKE_CODEX); mock.chmod(0o700)
        self.enterContext(patch.dict(os.environ, {
            'FAKE_CODEX_MODE':'offline', 'TMPDIR':str(self.tmp),
            'PATH':f'{self.tmp}:{os.environ["PATH"]}',
        }))
        self.enterContext(patch('tempfile.tempdir', str(self.tmp)))
        self.model=CodexModel(model='fake', binary=str(mock), timeout=5)
        self.request=ModelRequest('synthetic', [], response_schema={'type':'object'})

    def configure(self):
        cfg=ServiceConfig(db=str(self.tmp/'fixture.sqlite3'), provider='codex',
                          model='fake', review_model='fake', codex_job_timeout=5,
                          processing_session_ids=[], source_workers=1)
        store=self.enterContext(MemoryStore(cfg.db))
        return cfg.service(store)

    def test_known_outages_are_redacted_and_unknown_errors_remain_errors(self):
        for mode in ('offline','auth','transport'):
            with self.subTest(mode=mode), patch.dict(os.environ,FAKE_CODEX_MODE=mode):
                with self.assertRaises(ModelProviderUnavailable) as failure:
                    self.model(self.request)
                self.assertEqual(failure.exception.code,'model_provider_unavailable')
                self.assertNotIn('private',str(failure.exception))
                self.assertNotIn(PRIVATE_DIAGNOSTIC,str(failure.exception))
        with self.assertRaises(ModelProviderUnavailable):
            CodexModel(model='fake',binary=str(self.tmp/'missing-codex'))(self.request)
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired(
                ['codex'], 1, stderr=PRIVATE_DIAGNOSTIC)):
            with self.assertRaisesRegex(ModelProviderUnavailable,'timed out') as failure:
                self.model(self.request)
            self.assertNotIn(PRIVATE_DIAGNOSTIC,str(failure.exception))
            marker=f'ModelProviderUnavailable: {failure.exception}'
            self.assertEqual(persisted_model_error_code(marker),'model_provider_unavailable')
            self.assertIsNone(persisted_model_error_code(marker+' extra content'))
        for mode,pattern in [('validation','exited with code 2'),('bad-json','invalid event stream')]:
            with self.subTest(mode=mode), patch.dict(os.environ,FAKE_CODEX_MODE=mode):
                with self.assertRaisesRegex(ModelError,pattern) as failure:
                    self.model(self.request)
                self.assertNotIsInstance(failure.exception,ModelProviderUnavailable)
        with patch.dict(os.environ,FAKE_CODEX_MODE='recover-stream'):
            self.assertEqual(json.loads(self.model(self.request)),{'result':'fictional-success'})

    def test_job_status_no_false_card_and_controlled_recovery(self):
        service=self.configure()
        saved=service.thoughts.publish('Previously verified fictional card')
        session='synthetic-owner-run'
        r=service.capture_message(session,'message-one','Test Person','user',
                                  'The fictional lab uses a cardboard door.',0,'fixture')
        event_id=r['event']['id']
        w=TracedThoughtWorker(service.queue,service.engine,lock_owned=True,
                              processing_session_ids=(session,))
        w.run_once()
        state=service.status(event_id)
        self.assertEqual(state['job']['status'],'failed')
        self.assertEqual(state['job']['attempts'],1)
        self.assertEqual(state['job']['error_code'],'model_provider_unavailable')
        self.assertIn('model_provider_unavailable', {x.get('error_code') for x in state['errors']})
        self.assertNotIn(PRIVATE_DIAGNOSTIC,json.dumps(state))
        self.assertNotIn('private-provider-body',json.dumps(state))
        self.assertEqual(state['job']['extracted_ids'],[])
        self.assertEqual(service.thoughts.get(saved.id),saved)
        self.assertIn('cardboard door',service.queue.get_event(event_id).prompt)
        w.run_once()
        self.assertEqual(service.queue.get(event_id).attempts,1)
        service.queue.retry(event_id)
        with patch.dict(os.environ,FAKE_CODEX_MODE='success'):
            w.run_once()
        job=service.queue.get(event_id)
        self.assertEqual(job.status,'completed')
        self.assertEqual(len(job.extracted_ids),1)
        self.assertIn('cardboard door',service.thoughts.get(job.extracted_ids[0]).text)
        self.assertEqual(service.thoughts.get(saved.id),saved)

    def test_exact_group_failure_marks_only_requested_event(self):
        service=self.configure()
        session='synthetic-owner-run'
        events=[service.capture_message(session,str(n),'Test','user',f'Fictional text {n}.',n,'fixture')['event']['id']
                for n in range(2)]
        w=TracedThoughtWorker(service.queue,service.engine,lock_owned=True,processing_session_ids=(session,))
        w.process_source_group(events[:1],1)
        first=service.status(events[0])['job']
        self.assertEqual((first['status'],first['error_code'],first['extracted_ids']),
                         ('failed','model_provider_unavailable',[]))
        self.assertEqual(service.queue.get(events[1]).attempts,0)

    def test_query_distinguishes_unavailable_from_absence(self):
        service=self.configure()
        existing=service.thoughts.publish('Only after fictional review.', question='When can it open?',
                                          metadata={'verification':'accepted'})
        response=service.query('When can it open?')
        self.assertEqual(response['answer_error']['code'],'model_provider_unavailable')
        self.assertFalse(response['answer_accepted'])
        self.assertIsNone(response['answer'])
        self.assertNotIn('knowledge_limit', response)
        self.assertEqual(service.thoughts.get(existing.id),existing)

if __name__=='__main__':
    unittest.main()
