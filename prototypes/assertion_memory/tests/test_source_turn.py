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
assert mcp['enabled_tools']==['select_references','older_dialogue','semantic_search','thought_sources'] and mcp['required']
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
  if kind=='long':assert len(primary['primary_text'])>24000 and primary['primary_text'].endswith('TAIL🧩')
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
  if kind in ('session-context','session-forged'):
   foreign=rpc('older_dialogue',{'session_id':'context-chat','start_date':'2024-06-02','end_date':'2024-06-03'})['result']['structuredContent']
   assert [m['text'] for m in foreign['messages']]==[f'Chronological item {i}' for i in range(1,9)],foreign
   token=foreign['next_cursor']
   assert foreign['history_status']=='more' and token
   assert 'error' in rpc('older_dialogue',{'cursor':token,'session_id':'foreign-scope'})
   last=rpc('older_dialogue',{'cursor':token})['result']['structuredContent']
   assert [m['text'] for m in last['messages']]==['Chronological item 9'],last
   assert all('ref' in m and m['evidence_eligible'] is False for m in foreign['messages']+last['messages'])
   assert last['history_status']=='history_end' and not last['next_cursor']
   assert 'error' in rpc('older_dialogue',{'cursor':token})
   assert 'error' in rpc('older_dialogue',{'session_id':'foreign-scope'})
   assert 'error' in rpc('older_dialogue',{'session_id':'unknown-session'})
   local=rpc('older_dialogue',{'session_id':'current','start_date':'2024-06-02','end_date':'2024-06-03'})['result']['structuredContent']
   implicit=rpc('older_dialogue',{'start_date':'2024-06-02','end_date':'2024-06-03'})['result']['structuredContent']
   assert [r['text'] for r in local['messages']]==['Оля Н. раньше любила хорроры. 🧩'],local
   assert [r['text'] for r in implicit['messages']]==[local['messages'][0]['text']]
   assert local['messages'][0]['evidence_eligible'] and 'ref' in local['messages'][0]
  elif kind!='cross-navigation':
   assert 'error' in rpc('older_dialogue',{'session_id':'context-chat'})
  ref=next((r['ref'] for r in b['messages'] if 'Оля Н.' in r['text']),None)
  if kind in ('navigation','cross-navigation'):
   assert 'error' in rpc('thoughts_from_source',{'ref':ref})
   search=rpc('semantic_search',{'query':'билеты'})['result']['structuredContent']
   if kind=='cross-navigation':
    assert 'session_id="current"' in p['input'][0]['text']
    other,= [item for item in search['thoughts'] if 'чужой диалог' in item['text'].lower()]
    assert 'ref' in other and other['evidence_eligible'] is False
    assert 'error' in rpc('thought_sources',{'ref':other['text']})
    assert all('запрещён' not in item['text'] and 'будущее' not in item['text'] for item in search['thoughts'])
    current=rpc('semantic_search',{'query':'билеты','session_id':'current'})['result']['structuredContent']
    assert [item['text'] for item in current['thoughts']]==['Билеты на хоррор.']
    selected=rpc('semantic_search',{'query':'билеты','session_id':'other-dialogue'})['result']['structuredContent']
    assert [item['text'] for item in selected['thoughts']]==[other['text']]
    inaccessible=rpc('semantic_search',{'query':'билеты','session_id':'foreign-scope'})['result']['structuredContent']
    assert inaccessible['thoughts']==[]
    search=current
   else:
    inaccessible=rpc('semantic_search',{'query':'билеты','session_id':'other-dialogue'})['result']['structuredContent']
    assert inaccessible['thoughts']==[]
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
  if kind=='session-context':proposal['supports'][1]=local['messages'][0]['ref']
  if kind=='session-forged':proposal['supports'][1]=foreign['messages'][0]['text']
  if kind not in ('bad','session-forged'):
   assert 'result' in rpc('select_references',{'refs':proposal['supports']})
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

    def attempt(self,kind,*,revoke=False,retrieval=None,confined=None,prompt=None):
        with tempfile.TemporaryDirectory() as tmp,MemoryStore(Path(tmp)/'fixture.sqlite') as memory:
            code=Path(tmp)/'fixture.py'; code.write_text(FIXTURE)
            queue=CaptureQueue(memory);thoughts=ThoughtStore(memory)
            prior=queue.capture_event('dialogue','earlier','Оля Н. раньше любила хорроры. 🧩',
                                      source='fixture',scope='scope',position=1,recorded_at='2024-06-02T10:00:00Z')
            for n in range(2,12):
                queue.capture_event('dialogue',str(n),f'Context number {n}.',source='fixture',scope='scope',position=n)
            queue.capture_event('dialogue','private','DO NOT DISCLOSE',source='fixture',scope='scope',position=13,
                                metadata={'visibility':'private','subagent_id':'internal','recipient':'worker'})
            queue.capture_event('dialogue','other','DO NOT DISCLOSE',source='fixture',scope='other',position=14)
            if kind in ('session-context','session-forged'):
                for n in range(12):
                    stamp='2024-06-02T12:00:00Z' if 1<=n<=9 else ('2024-06-01T00:00:00Z' if n==0 else '2024-06-03T00:00:00Z')
                    queue.capture_event('capture-extra','test-'+str(n),f'Chronological item {n}',source='fixture',
                        scope='scope',position=n+1,recorded_at=stamp,metadata={'native_session_id':'context-chat'})
                queue.capture_event('capture-extra','secret','Private chat context',source='fixture',scope='scope',position=20,
                    recorded_at='2024-06-02T12:01:00Z',metadata={'native_session_id':'context-chat','visibility':'private'})
            foreign=queue.capture_event('other-dialogue','seed','Foreign synthetic raw event',
                                         source='fixture',scope='scope',position=1)
            outside=queue.capture_event('foreign-scope','seed','Another scope raw event',
                                         source='fixture',scope='unauthorized',position=1)
            source=queue.capture_event('dialogue','target',prompt or 'Теперь она больше не любит этот жанр.',
                                       source='fixture',scope='scope',position=20)
            equal_later=queue.capture_event('other-dialogue','equal','Equal timestamp but inserted after primary.',
                                            source='fixture',scope='scope',position=3,recorded_at=source.recorded_at)
            future=queue.capture_event('future-dialogue','seed','Future synthetic raw event',
                                       source='fixture',scope='scope',position=1,recorded_at='2099-01-01T00:00:00Z')
            hidden=queue.capture_event('other-dialogue','private','Private synthetic raw event',
                                       source='fixture',scope='scope',position=2,metadata={'visibility':'private'})
            calls=[]
            def verifier(request):
                calls.append(request.task)
                self.assertEqual(request.task,'Verify thought') # no legacy Construct or generation helper
                self.assertIn(prior.prompt[:-2] if kind in ('navigation','cross-navigation') else prior.prompt,[''.join(f['text'] for f in r.get('fragments',[])) for r in request.records])
                if revoke:
                    with memory.transaction(write=True) as db:
                        db.execute("UPDATE memory_capture_jobs SET lease_token='replaced' WHERE id=?",(source.id,))
                return json.dumps({'verdict':'accept','text':'Synthetic check accepted.'})
            engine=ThoughtEngine(thoughts,verifier,retrieval=retrieval)
            fixtures=[]
            if kind in ('navigation','cross-navigation'):
                for event,summary in (
                    (prior,'Билеты на хоррор.'),
                    (foreign,'Билеты: чужой диалог.'),
                    (outside,'Билеты: запрещённая область.'),
                    (future,'Билеты: будущее.'),
                    (equal_later,'Билеты: будущее по порядку при том же времени.'),
                    (hidden,'Билеты: запрещённая приватная запись.'),
                ):
                    fixtures.append(thoughts.publish(summary,origin=engine._source_packet(event)[2],
                                    bases=[{'event_id':event.id,'span':{'start':0,'end':len(event.prompt)-2}}],
                                    metadata={'verification':'accepted'}).id)
                fixtures.append(thoughts.publish('Indirect is not a direct interpretation',
                                origin={'kind':'inference'},bases=[{'thought_id':fixtures[0],'version':1}],
                                metadata={'verification':'accepted'}).id)
            with memory.transaction(write=True) as db:queue._scheduler_schema(db)
            extras=(str(memory.path),*confined) if confined is not None else ()
            from ..turn_source.navigation import OwnerScopeGrant
            grant=OwnerScopeGrant(Path(memory.path),'scope') if kind in ('cross-navigation','session-context','session-forged') else None
            config=SourceTurnConfig((sys.executable,str(code),kind,*extras),enabled=True,owner_scope=grant)
            factory=(SourceTurnFactory._for_protocol_fixture(config) if confined is None
                     else SourceTurnFactory._for_confined_test(config))
            worker=ThoughtWorker(queue,engine,lane='source',claim_bound_source_extractor_factory=factory)
            job=worker._source(worker._claim_source(event_id=source.id))
            saved=[row for row in thoughts.list_thoughts() if row.id not in fixtures]
            if kind in ('normal','long','navigation','cross-navigation','session-context') and not revoke:
                self.assertEqual(job.status,'completed',job.error)
                result,=saved
                self.assertEqual(job.extracted_ids,[result.id])
                self.assertEqual({b['event_id'] for b in result.bases},{prior.id,source.id})
                self.assertTrue(all('content_hash' in basis for basis in result.bases))
                if kind in ('navigation','cross-navigation'):
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

    def test_authorized_chronological_dialogue_has_no_foreign_proof(self):
        job,calls=self.attempt('session-context')
        self.assertEqual((job.status,calls),('completed',['Verify thought']))
        job,calls=self.attempt('session-forged')
        self.assertEqual((job.extracted_ids,calls),([],[]))
        self.assertIn('Verified primary and exact shown evidence',job.error)

    def test_cross_dialogue_frozen_watermark_hides_late_backdated_event(self):
        from ..turn_source.reader import EarlierDialogue
        from ..turn_source.navigation import OwnerScopeGrant
        from ..turn_source.session_dialogue import SessionDialogue
        with tempfile.TemporaryDirectory() as tmp,MemoryStore(Path(tmp)/'fixture.sqlite') as memory:
            queue=CaptureQueue(memory); engine=ThoughtEngine(ThoughtStore(memory),lambda _:None)
            queue.capture_event('elsewhere','safe','Already visible.',source='fixture',scope='scope',
                                recorded_at='2024-01-02')
            primary=queue.capture_event('dialogue','primary','Check the other chat.',source='fixture',scope='scope',
                                        recorded_at='2024-01-03')
            waiting=queue.capture_event('elsewhere','waiting','Awaiting admission at attempt start.',
                                       source='fixture',scope='scope',recorded_at='2024-01-02',await_origin=True)
            unpositioned=queue.capture_event('elsewhere','reposition','Position pending on saved source.',
                                             source='fixture',scope='scope',recorded_at='2024-01-02')
            private=queue.capture_event('elsewhere','private','Private tail.',source='fixture',scope='scope',
                                        recorded_at='2024-01-01',metadata={'visibility':'private'})
            with memory.transaction(write=True) as db:queue._scheduler_schema(db)
            factory=SourceTurnFactory._for_protocol_fixture(SourceTurnConfig((sys.executable,),enabled=True))
            worker=ThoughtWorker(queue,engine,lane='source',claim_bound_source_extractor_factory=factory)
            claim=worker._claim_source(event_id=primary.id)
            with worker._source_extractor(primary,claim) as extractor:
                reader=EarlierDialogue(memory.path,extractor.request,primary,freeze_membership=True)
                self.addCleanup(reader.close)
                dialogue=SessionDialogue(reader,OwnerScopeGrant(Path(memory.path),'scope'))
                with memory.transaction(write=True) as db:
                    old=db.execute('SELECT rowid FROM memory_raw_events WHERE id=?',(private.id,)).fetchone()[0]
                    db.execute('DELETE FROM memory_capture_jobs WHERE event_id=?',(private.id,))
                    db.execute('DELETE FROM memory_raw_events WHERE id=?',(private.id,))
                late=queue.capture_event('elsewhere','late','Backdated but not part of the attempt.',
                                         source='fixture',scope='scope',recorded_at='2024-01-01')
                with memory.transaction() as db:
                    self.assertEqual(db.execute('SELECT rowid FROM memory_raw_events WHERE id=?',(late.id,)).fetchone()[0],old)
                self.assertEqual(queue.capture_event('elsewhere','waiting',waiting.prompt,source='fixture',
                                 scope='scope',recorded_at=waiting.recorded_at).id,waiting.id)
                self.assertEqual(queue.capture_event('elsewhere','reposition',unpositioned.prompt,source='fixture',
                                 scope='scope',recorded_at=unpositioned.recorded_at,position=10).id,unpositioned.id)
                with memory.transaction() as db:
                    self.assertNotEqual(db.execute('SELECT status FROM memory_capture_jobs WHERE event_id=?',
                                                   (waiting.id,)).fetchone()[0],'waiting_origin')
                    self.assertEqual(db.execute('SELECT position FROM memory_event_positions WHERE event_id=?',
                                                (unpositioned.id,)).fetchone()[0],10)
                page=dialogue.read({'session_id':'elsewhere'})
                self.assertEqual([row['text'] for row in page['messages']],['Already visible.'])
                from contextlib import contextmanager
                real=reader.snapshot
                snapshots=iter((False,True))
                @contextmanager
                def invalidate_on_final_snapshot():
                    if next(snapshots):
                        with memory.transaction(write=True) as db:
                            db.execute("UPDATE memory_capture_jobs SET status='waiting_origin' WHERE event_id IN "
                                       "(SELECT id FROM memory_raw_events WHERE turn_id='safe')")
                    with real() as db:
                        yield db
                from ..turn_source.reader import SourceReadDenied
                with patch.object(reader,'snapshot',invalidate_on_final_snapshot):
                    with self.assertRaisesRegex(SourceReadDenied,'changed during navigation'):
                        dialogue.read({'session_id':'elsewhere'})

    def test_cross_dialogue_search_is_scoped_and_foreign_thought_has_no_raw_receipt(self):
        job,verification=self.attempt('cross-navigation')
        self.assertEqual((job.status,verification),('completed',['Verify thought']))

    def test_forged_receipt_or_lost_lease_cannot_publish(self):
        job,calls=self.attempt('bad')
        self.assertEqual(calls,[])
        self.assertEqual(job.extracted_ids,[])
        job,calls=self.attempt('normal',revoke=True)
        self.assertEqual(calls,['Verify thought'])
        self.assertEqual(job.extracted_ids,[])

    def test_unresolved_remains_pending_without_writing_a_thought(self):
        job,calls=self.attempt('unresolved')
        self.assertNotEqual(job.status,'completed',job.error)
        self.assertEqual((job.extracted_ids,calls),([],[]))
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
            self.assertEqual(set(mcp.tools),{'select_references','older_dialogue','semantic_search','thought_sources'})
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
                self.assertNotEqual(job.status,'completed',job.error)
        self.assertEqual((job.extracted_ids,calls),([],[]))

    def test_unknown_agent_message_phase_cannot_publish(self):
        job,calls=self.attempt('unknown-phase')
        self.assertEqual((job.extracted_ids,calls),([],[]))
        self.assertIn('Expected one structured proposal',job.error)

    def test_rejects_extra_server_or_tool_before_model_turn(self):
        for kind in ('extra-server','extra-tool'):
            job,calls=self.attempt(kind)
            self.assertEqual((job.extracted_ids,calls),([],[]))
            self.assertRegex(job.error,'(inventory|unavailable)')

    def test_long_primary_through_real_stdio_fake_process(self):
        job,calls=self.attempt('long',prompt='Synthetic source. '*1700+'TAIL🧩')
        self.assertEqual(job.status,'completed',job.error)
        self.assertEqual(calls,['Verify thought'])


if __name__=='__main__':unittest.main()
