"""Explicit opt-in of the real worker with a deterministic subprocess fixture."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from ..model import ModelError
from ..queue import CaptureQueue
from ..storage import MemoryStore
from ..thought_engine import ThoughtEngine
from ..thought_store import ThoughtStore
from ..thought_worker import ThoughtWorker
from ..turn_source.extractor import SourceTurnConfig,SourceTurnFactory

FIXTURE=r'''
import json,os,sys,subprocess,tomllib
from pathlib import Path
home=Path(os.environ['CODEX_HOME'])
config=tomllib.loads((home/'config.toml').read_text())
mcp=config['mcp_servers']['source_job']
assert mcp['enabled_tools']==['older_dialogue','semantic_search','thought_sources'] and mcp['required']
assert not (home/'auth.json').exists()
kind=sys.argv[1]
if len(sys.argv)>2:
 import socket
 assert not Path(sys.argv[2]).exists(), 'host DB exposed'
 assert not Path(sys.argv[3]).exists(), 'parent cwd/secret exposed'
 assert 'OUTSIDE_SENTINEL' not in os.environ
 assert os.readlink('/proc/self/ns/net')!=sys.argv[4]
 assert os.readlink('/proc/self/ns/pid')!=sys.argv[5]
 assert not Path('/home/agent').exists(), 'host home exposed'
 try:
  with socket.create_connection(('127.0.0.1',int(sys.argv[6])),timeout=.1): pass
 except OSError: pass
 else: raise AssertionError('accessible host-network port')
def send(msg):print(json.dumps(msg,ensure_ascii=False),flush=True)
for row in sys.stdin:
 m=json.loads(row);p=m.get('params',{});i=m.get('id')
 if m['method']=='initialize':send({'id':i,'result':{}})
 if m['method']=='thread/start':
  assert p['ephemeral'] and p['environments']==[] and p['approvalPolicy']=='never'
  send({'id':i,'result':{'thread':{'id':'fiction-thread','ephemeral':True},
                          'sandbox':{'type':'readOnly','networkAccess':False}}})
 if m['method']=='mcpServerStatus/list':
  assert p['threadId']=='fiction-thread'
  rows=[{'name':'source_job','tools':{name:{} for name in mcp['enabled_tools']}}]
  if kind=='extra-server' and 'serverName' not in p:rows.append({'name':'forbidden','tools':{}})
  if kind=='extra-tool' and 'serverName' in p:rows[0]['tools']['forbidden']={}
  send({'id':i,'result':{'data':rows}})
 if m['method']=='turn/start':
  assert p['threadId']=='fiction-thread' and p['outputSchema']['required']==['status','text','supports']
  send({'id':i,'result':{'turn':{'id':'fiction-turn'}}})
  primary=json.loads(p['input'][0]['text'].split('Primary packet: ',1)[1])
  relay=subprocess.Popen([mcp['command'],*mcp['args']],text=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE)
  def rpc(name,args):
   relay.stdin.write(json.dumps({'id':1,'jsonrpc':'2.0','method':'tools/call','params':{'name':name,'arguments':args}})+'\n')
   relay.stdin.flush()
   return json.loads(relay.stdout.readline())
  def query(cursor=None):
   return rpc('older_dialogue',{} if cursor is None else {'cursor':cursor})['result']['structuredContent']
  a=query()
  if kind=='budget':
   assert a['history_status']=='attempt_budget_exhausted' and a['next_cursor'] is None and len(a['messages'])==8
   b={'messages':[]}
  else:
   assert a['history_status']=='more'
   b=query(a['next_cursor'])
   assert b['history_status']=='history_end' and b['next_cursor'] is None
  assert not any('DO NOT DISCLOSE' in r['text'] for r in [*a['messages'],*b['messages']])
  ref=next((r['ref'] for r in b['messages'] if 'Оля Н.' in r['text']),None)
  if kind=='navigation':
   assert 'error' in rpc('thoughts_from_source',{'ref':ref})
   search=rpc('semantic_search',{'query':'билеты'})['result']['structuredContent']
   found,=search['thoughts']
   assert found['text']=='Билеты на хоррор.' and found['version']==1 and found['ref']
   assert 'error' in rpc('thought_sources',{'ref':ref})
   assert 'error' in rpc('thought_sources',{'ref':'forged'})
   sources=rpc('thought_sources',{'ref':found['ref']})['result']['structuredContent']
   exact,=sources['sources']
   assert exact['text']=='Оля Н. раньше любила хорроры.'
   assert exact['role']=='user' and exact['ref']!=ref
   ref=exact['ref']
  proposal={'status':'proposal','text':'Оля Н. больше не любит хорроры.','supports':[primary['primary_ref'],ref]}
  if kind=='bad':proposal['supports'][1]='forged-ref'
  if kind in ('unresolved','budget'):proposal={'status':'unresolved','text':None,'supports':[]}
  relay.terminate(); relay.wait(2)
  send({'method':'item/completed','params':{'threadId':'fiction-thread','turnId':'fiction-turn',
    'item':{'id':'message','type':'agentMessage','phase':('unknown' if kind=='unknown-phase' else 'final_answer'),'text':json.dumps(proposal,ensure_ascii=False)}}})
  send({'method':'turn/completed','params':{'threadId':'fiction-thread',
    'turn':{'id':'fiction-turn','items':[],'status':'completed'}}})
'''

class SourceTurnTest(unittest.TestCase):
    def test_explicit_factory_and_registered_tool(self):
        with self.assertRaises(ValueError):SourceTurnConfig((sys.executable,))

        with self.assertRaisesRegex(ValueError,"OS-isolated launcher"):
            SourceTurnFactory(SourceTurnConfig((sys.executable,),enabled=True))

    def attempt(self,kind,*,revoke=False,retrieval=None,confined=None):
        with tempfile.TemporaryDirectory() as tmp,MemoryStore(Path(tmp)/'fixture.sqlite') as memory:
            code=Path(tmp)/'fixture.py'; code.write_text(FIXTURE)
            queue=CaptureQueue(memory);thoughts=ThoughtStore(memory)
            prior=queue.capture_event('dialogue','earlier','Оля Н. раньше любила хорроры. 🧩',
                                      source='fixture',scope='scope',position=1)
            for n in range(2,12):
                queue.capture_event('dialogue',str(n),f'Context number {n}.',source='fixture',scope='scope',position=n)
            queue.capture_event('dialogue','private','DO NOT DISCLOSE',source='fixture',scope='scope',position=13,
                                metadata={'visibility':'private','subagent_id':'internal','recipient':'worker'})
            queue.capture_event('dialogue','other','DO NOT DISCLOSE',source='fixture',scope='other',position=14)
            source=queue.capture_event('dialogue','target','Теперь она больше не любит этот жанр.',
                                       source='fixture',scope='scope',position=20)
            calls=[]
            def verifier(request):
                calls.append(request.task)
                self.assertEqual(request.task,'Verify thought') # no legacy Construct or generation helper
                self.assertIn(prior.prompt[:-2] if kind=='navigation' else prior.prompt,[''.join(f['text'] for f in r.get('fragments',[])) for r in request.records])
                if revoke:
                    with memory.transaction(write=True) as db:
                        db.execute("UPDATE memory_capture_jobs SET lease_token='replaced' WHERE id=?",(source.id,))
                return json.dumps({'verdict':'accept','text':'Synthetic check accepted.'})
            engine=ThoughtEngine(thoughts,verifier,retrieval=retrieval)
            fixtures=[]
            if kind=='navigation':
                for event,summary in (
                    (prior,'Билеты на хоррор.'),
                    (queue.capture_event('other-dialogue','seed','bilet',source='fixture',scope='scope',position=1),
                     'Forbidden cross-dialogue result'),
                ):
                    fixtures.append(thoughts.publish(summary,origin=engine._source_packet(event)[2],
                                    bases=[{'event_id':event.id,'span':{'start':0,'end':len(event.prompt)-2}}],
                                    metadata={'verification':'accepted'}).id)
                fixtures.append(thoughts.publish('Indirect is not a direct interpretation',
                                origin={'kind':'inference'},bases=[{'thought_id':fixtures[0],'version':1}],
                                metadata={'verification':'accepted'}).id)
            with memory.transaction(write=True) as db:queue._scheduler_schema(db)
            extras=(str(memory.path),*confined) if confined is not None else ()
            config=SourceTurnConfig((sys.executable,str(code),kind,*extras),enabled=True)
            factory=(SourceTurnFactory._for_protocol_fixture(config) if confined is None
                     else SourceTurnFactory._for_confined_test(config))
            worker=ThoughtWorker(queue,engine,lane='source',claim_bound_source_extractor_factory=factory)
            job=worker._source(worker._claim_source(event_id=source.id))
            saved=[row for row in thoughts.list_thoughts() if row.id not in fixtures]
            if kind in ('normal','navigation') and not revoke:
                self.assertEqual(job.status,'completed',job.error)
                result,=saved
                self.assertEqual(job.extracted_ids,[result.id])
                self.assertEqual({b['event_id'] for b in result.bases},{prior.id,source.id})
                self.assertTrue(all('content_hash' in basis for basis in result.bases))
                if kind=='navigation':
                    spans=[b['span'] for b in result.bases if b['event_id']==prior.id]
                    self.assertEqual(''.join(prior.prompt[s['start']:s['end']] for s in spans),prior.prompt[:-2])
                    self.assertEqual(min(s['start'] for s in spans),0)
                    self.assertEqual(max(s['end'] for s in spans),len(prior.prompt)-2)
                self.assertIsNone(worker._claim_source(event_id=source.id))
            else:self.assertEqual(saved,[])
            return job,calls

    def test_one_turn_exact_history_then_existing_verifier_publisher(self):
        job,calls=self.attempt('normal')
        self.assertEqual(calls,['Verify thought'])

    def test_one_turn_history_source_thought_search_and_canonical_publication(self):
        from ..thought_retrieval import ThoughtRetrieval
        from ..model import CandidateSearch
        shared=ThoughtRetrieval(CandidateSearch())
        original=ThoughtRetrieval.rank_entries
        calls=[]
        def rank(instance,query,entries,limit):
            calls.append((instance,query,[v['text'] for v in entries.values()]))
            return original(instance,query,entries,limit)
        with patch.object(ThoughtRetrieval,'rank_entries',rank):
            job,verification=self.attempt('navigation',retrieval=shared)
        self.assertEqual((job.status,verification),('completed',['Verify thought']))
        self.assertIs(calls[0][0],shared)
        self.assertEqual([(query,rows) for _,query,rows in calls],[('билеты',['Билеты на хоррор.'])])

    def test_forged_receipt_or_lost_lease_cannot_publish(self):
        job,calls=self.attempt('bad')
        self.assertEqual(calls,[])
        self.assertEqual(job.extracted_ids,[])
        job,calls=self.attempt('normal',revoke=True)
        self.assertEqual(calls,['Verify thought'])
        self.assertEqual(job.extracted_ids,[])

    def test_unresolved_finishes_without_writing_a_thought(self):
        job,calls=self.attempt('unresolved')
        self.assertEqual((job.status,job.extracted_ids,calls),('completed',[],[]),job.error)
    @unittest.skipUnless(os.environ.get('ASSERTION_OFFLINE_CODEX_CLI'),
                         'Requires an explicitly selected installed Codex CLI, no model turn')
    def test_installed_cli_offline_registry_handshake(self):
        from ..turn_source.extractor import SourceMCP, ContinuingSourceExtractor
        from ..turn_source.navigation import ClaimNavigation
        from ..turn_source.transport import StdioTransport
        command=(os.environ['ASSERTION_OFFLINE_CODEX_CLI'],'app-server','--listen','stdio://',
                 '-c','model="fictional-abi-only"','-c','model_provider="abi_offline"',
                 '-c','model_providers.abi_offline={name="Offline",base_url="http://127.0.0.1:1/v1",wire_api="responses"}')
        wire=StdioTransport(command,allow_spawn=True,timeout=8)
        try:
            mcp=SourceMCP(None,ClaimNavigation(None,None))
            wire.tools=list(mcp.tools)
            wire.configure_mcp(mcp.dispatch)
            wire.call('initialize',{'clientInfo':{'name':'offline-source-test','version':'0.1'},
                                    'capabilities':{'experimentalApi':True}})
            wire.notify('initialized',{})
            result=wire.call('thread/start',{'ephemeral':True,'sandbox':'read-only','approvalPolicy':'never',
                             'environments':[],'cwd':wire.cwd})
            self.assertTrue(result['thread']['ephemeral'])
            self.assertEqual(result['thread']['modelProvider'],'abi_offline')
            self.assertEqual(result['sandbox'],{'type':'readOnly','networkAccess':False})
            self.assertEqual(set(mcp.tools),{'older_dialogue','semantic_search','thought_sources'})
            ContinuingSourceExtractor.check_registry(wire,result['thread']['id'],mcp.tools)
        finally:wire.close()

    def test_recapture_with_position_invalidates_live_rowid_history(self):
        from ..turn_source.reader import EarlierDialogue, SourceReadDenied
        with tempfile.TemporaryDirectory() as tmp,MemoryStore(Path(tmp)/'fixture.sqlite') as memory:
            queue=CaptureQueue(memory); engine=ThoughtEngine(ThoughtStore(memory),lambda _:None)
            queue.capture_event('dialogue','extra','A context that arrived before the primary.',source='fixture',scope='scope')
            primary=queue.capture_event('dialogue','primary','Earlier chronological message.',source='fixture',scope='scope')
            with memory.transaction(write=True) as db:queue._scheduler_schema(db)
            factory=SourceTurnFactory._for_protocol_fixture(SourceTurnConfig((sys.executable,),enabled=True))
            worker=ThoughtWorker(queue,engine,lane='source',claim_bound_source_extractor_factory=factory)
            claim=worker._claim_source(event_id=primary.id)
            with worker._source_extractor(primary,claim) as extractor:
                reader=EarlierDialogue(memory.path,extractor.request,primary)
                future=queue.capture_event('dialogue','extra','A context that arrived before the primary.',
                                          source='fixture',scope='scope',position=20)
                self.assertNotEqual(future.id,primary.id)
                with self.assertRaisesRegex(SourceReadDenied,'Mixed-position'):
                    reader.older(None)
                recaptured=queue.capture_event('dialogue','primary','Earlier chronological message.',
                                               source='fixture',scope='scope',position=10)
                self.assertEqual(recaptured.id,primary.id)
                with self.assertRaisesRegex(SourceReadDenied,'order or eligibility changed'):
                    reader.older(None)
                with self.assertRaisesRegex(SourceReadDenied,'order or eligibility changed'):
                    reader.evidence([reader.primary_ref])
                with self.assertRaisesRegex(SourceReadDenied,'legacy partial'):
                    extractor.resume(primary,None,[],{})

    @unittest.skipUnless(os.getenv('ASSERTION_OFFLINE_OS_JAIL')=='1','Explicit Linux sandbox integration test')
    def test_os_confined_process_only_uses_claim_broker(self):
        import socket
        with tempfile.TemporaryDirectory() as tmp, socket.socket() as host:
            host.bind(('127.0.0.1',0));host.listen()
            marker=Path(tmp)/'host-secret';marker.write_text('fixture-only')
            args=(str(marker),os.readlink('/proc/self/ns/net'),os.readlink('/proc/self/ns/pid'),str(host.getsockname()[1]))
            with patch.dict(os.environ,{'OUTSIDE_SENTINEL':'do not inherit'}):
                job,verified=self.attempt('navigation',confined=args)
            self.assertEqual((job.status,verified),('completed',['Verify thought']))

    def test_offline_jail_failure_does_not_fall_back_to_open_process(self):
        from ..turn_source.offline_test_process import OfflineTestProcess
        with patch.object(OfflineTestProcess,'spawn',side_effect=OSError('synthetic launcher refused')) as spawn:
            job,verified=self.attempt('unresolved',confined=())
        spawn.assert_called_once()
        self.assertIn('synthetic launcher refused',job.error)
        self.assertEqual((job.extracted_ids,verified),([],[]))

    def test_attempt_ceiling_is_not_history_end(self):
        from ..turn_source.reader import EarlierDialogue
        for cap,limit in [('MAX_PAGES',1),('MAX_REFS',9),('MAX_BYTES',48100)]:
            with self.subTest(cap=cap),patch.object(EarlierDialogue,cap,limit):
                job,calls=self.attempt('budget')
                self.assertEqual((job.status,job.extracted_ids,calls),('completed',[],[]),job.error)

    def test_unknown_agent_message_phase_cannot_publish(self):
        job,calls=self.attempt('unknown-phase')
        self.assertEqual((job.extracted_ids,calls),([],[]))
        self.assertIn('Expected one structured proposal',job.error)

    def test_rejects_extra_server_or_tool_before_model_turn(self):
        for kind in ('extra-server','extra-tool'):
            job,calls=self.attempt(kind)
            self.assertEqual((job.extracted_ids,calls),([],[]))
            self.assertRegex(job.error,'(inventory|unavailable)')


if __name__=='__main__':unittest.main()
