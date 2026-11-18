"""Outbound transport completeness fixtures, not semantic-model accuracy."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    ClientSession = None

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore

ROOT=Path(__file__).resolve().parents[1]
BUDGET=28_000


@asynccontextmanager
async def connect(db,model=False):
    extra=[]
    if model:
        script="""import sys,json
p=sys.stdin.read()
with open(sys.argv[1],'a') as log:
    log.write(json.dumps(p)+'\\n')
v=json.loads(p.split('\\n\\n',1)[1])
if v['task'] == 'Review grounded memory contribution':
    print(json.dumps({'grounding_status':'accepted','coverage':'packet_claims',
                     'reason':'Grounded transport fixture contribution.',
                     'issues':[],'context_requests':[],'global_requests':[]}))
elif v['task'] in ('Review grounded memory overview','Review memory overview assembly'):
    print(json.dumps({'status':'accepted','coverage':'all_claims',
                     'reason':'Transport fixture only; not a semantic accuracy verdict.',
                     'issues':[],'context_requests':[],'unknown_needs':[]}))
elif v['task'] == 'Synthesize grounded memory overview':
    print(' '.join(r['text'] for r in v['records'] if r.get('role')=='verified_contribution'))
else:
    conditions = ['Condition for ['+e['from_id']+'] relative to ['+e['to_id']+']: '+e['metadata']['condition']
                  for r in v['records'] if r.get('role')=='evidence_edges' for e in r['edges']
                  if e.get('metadata',{}).get('condition')]
    print('Fixture summary: '+','.join('['+r['id']+']' for r in v['records'] if 'id' in r and 'text' in r)
          +'\\n'+'\\n'.join(conditions))
"""
        if model=='canonical':
            from tests.test_mcp_service import MODEL_SCRIPT
            script=MODEL_SCRIPT
        extra=['--model-command-json',json.dumps([sys.executable,'-c',script,str(db)+'.requests'])]
    params=StdioServerParameters(command=sys.executable,args=[
        '-m','prototypes.assertion_memory.mcp_server','--db',str(db),'--provider','subprocess' if model else 'none','--no-background',*extra],
        env={**os.environ,'PYTHONPATH':str(ROOT)},cwd=str(ROOT))
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            await client.initialize()
            yield client


@unittest.skipIf(ClientSession is None,'Install requirements-mcp.txt for official SDK tests')
class BoundedToolsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'memory.sqlite3'

    async def call(self,client,name,args):
        response=await client.call_tool(name,args)
        self.assertFalse(response.isError,response.content)
        self.assertLessEqual(len(response.content[0].text),BUDGET)
        self.assertLessEqual(len(response.model_dump_json(exclude_none=True)),BUDGET)
        return json.loads(response.content[0].text)

    def seed(self,count=90):
        with MemoryStore(self.db) as store:
            service=MemoryService(store)
            for i in range(count):
                key=f'rule-{i:03}'
                text=f'Restriction {i}: preserve condition {i}. '+('Exact explanatory text. '*40)
                event=service.capture_message('fixture',str(i),'fixture-author','user',text,i,
                                              f'fixture://{i}','transport')['event']
                store.append_assertion(assertion_id=key,text=text,actor='fixture-author',source=f'fixture://{i}',
                    episode='fixture',scope='transport',recorded_at='2026-01-01',metadata={
                    'kind':'decision','evidence_refs':[{'event_id':event['id'],'start':0,'end':len(text),'role':'user'}]})
                if i%2:
                    store.append_edge(edge_id=f'condition-{i}',from_id=key,to_id=f'rule-{i-1:03}',kind='exception_to',
                        recorded_at='2026-01-01',metadata={'condition':f'Only after condition {i}.'})
                if i>=2 and i%2==0:
                    store.append_edge(edge_id=f'navigation-{i}',from_id=key,to_id=f'rule-{i-2:03}',kind='supports',
                        recorded_at='2026-01-01')
            engine=service.engine
            engine.define_view(ViewSpec('rules','Rules','transport rules',scopes=('transport',),
                                       root_ids=tuple(f'rule-{i:03}' for i in range(count))))
            return engine.build_view('rules').id

    async def collect(self,client,continuation):
        parts=[]
        seen=[]
        while continuation:
            page=await self.call(client,continuation['tool'],continuation['arguments'])
            seen.append(page['offset'])
            parts.append(page['content'])
            continuation=page.get('continuation')
        self.assertEqual(seen,sorted(set(seen)))
        return ''.join(parts)

    async def pending_ids(self,client,cursor):
        ids=[]
        while cursor:
            page=await self.call(client,cursor['tool'],cursor['arguments'])
            ids.extend(row['job_id'] for row in page['pending_jobs'])
            options=page.get('continuations',[])
            cursor=next((c for c in options if c['arguments'].get('offset',0) > page['pagination']['pending_jobs']['offset']),None)
        self.assertEqual(len(ids),len(set(ids)))
        return ids

    async def test_large_query_pages_preserve_all_ids_conditions_and_old_snapshot(self):
        version=self.seed()
        async with connect(self.db,model=True) as client:
            query=await self.call(client,'memory_query',{'query':'transport rules','scope':'transport'})
            self.assertFalse(query['response_complete'])
            self.assertNotIn('generation_error',query)
            self.assertEqual(query['freshness_check']['review_status'],'accepted')
            diagnostics=next(c for c in query['continuations'] if c['tool']=='memory_status')
            self.assertEqual(len(await self.pending_ids(client,diagnostics)),90)
            version=query['view']['id']
            prompts=[json.loads(line) for line in Path(str(self.db)+'.requests').read_text().splitlines()]
            self.assertGreater(len(prompts),1)
            self.assertTrue(all(len(prompt)<=60000 for prompt in prompts))
            projected_edges=[edge for prompt in prompts
                for record in json.loads(prompt.split('\n\n',1)[1])['records']
                if record.get('role')=='evidence_edges' for edge in record['edges']]
            self.assertEqual({edge['id'] for edge in projected_edges if edge['kind']=='exception_to'},
                             {f'condition-{i}' for i in range(1,90,2)})
            self.assertTrue(all('recorded_at' not in edge for edge in projected_edges))
            sections={item['arguments']['section']:item for item in query['continuations']
                      if item['tool']=='memory_history'}
            evidence=json.loads(await self.collect(client,sections['evidence']))
            edges=json.loads(await self.collect(client,sections['edges']))
            summary=await self.collect(client,sections['summary'])
            self.assertEqual(len(evidence),90)
            self.assertEqual(len({row['id'] for row in evidence}),90)
            for i in range(90):
                self.assertIn(f'rule-{i:03}',summary)
            self.assertEqual(len([edge for edge in edges if edge['kind']=='exception_to']),45)
            for i in range(1,90,2):
                condition=f'Only after condition {i}.'
                self.assertIn(condition,json.dumps(edges))
                self.assertIn(condition,summary)
            self.assertTrue(all(edge.get('recorded_at') for edge in edges))
            with MemoryStore(self.db) as store:
                store.append_assertion(assertion_id='new',text='New later report.',actor='fixture',source='fixture://new',
                    episode='later',scope='transport',recorded_at='2026-02-01')
                store.append_edge(edge_id='new-condition',from_id='new',to_id='rule-000',kind='exception_to',
                    recorded_at='2026-02-01',metadata={'condition':'Later condition.'})
                MemoryEngine(store).build_view('rules')
            old=json.loads(await self.collect(client,sections['evidence']))
            self.assertEqual(old,evidence)
            self.assertEqual(json.loads(await self.collect(client,sections['edges'])),edges)
            self.assertEqual(await self.collect(client,sections['summary']),summary)

    async def test_many_versions_are_explicitly_paged_and_large_source_spans_reassemble(self):
        self.seed(2)
        with MemoryStore(self.db) as store:
            engine=MemoryEngine(store)
            for i in range(20):
                store.append_assertion(assertion_id=f'v{i}',text='transport '+('version detail '*80),actor='fixture',
                    source=f'fixture://v{i}',episode='versions',scope='transport',recorded_at=f'2026-02-{i+1:02}')
                store.append_edge(edge_id=f'v-condition-{i}',from_id=f'v{i}',to_id='rule-000',kind='exception_to',
                    recorded_at=f'2026-02-{i+1:02}',metadata={'condition':f'Version condition {i}.'})
                engine.build_view('rules')
            long='Unicode начало. '+('Long exact source body. '*6000)+' Last condition must survive.'
            service=MemoryService(store)
            event=service.capture_message('large','1','fixture','user',long,0,'fixture://large','large')['event']
            store.append_assertion(assertion_id='large',text=long,actor='fixture',source='fixture://large',episode='large',
                scope='large',recorded_at='2026-01-01',metadata={'evidence_refs':[{
                    'event_id':event['id'],'start':0,'end':len(long),'role':'user'}]})
        async with connect(self.db) as client:
            history=await self.call(client,'memory_history',{'view_id':'rules'})
            self.assertFalse(history['response_complete'])
            with MemoryStore(self.db) as store:
                store.append_assertion(assertion_id='after-manifest',text='Later rule.',actor='fixture',
                    source='fixture://late',episode='late',scope='transport',recorded_at='2026-03-01')
                store.append_edge(edge_id='late-condition',from_id='after-manifest',to_id='rule-000',kind='exception_to',
                    recorded_at='2026-03-01',metadata={'condition':'A later condition.'})
                MemoryEngine(store).build_view('rules')
            versions=json.loads(await self.collect(client,next(c for c in history['continuations']
                if c['arguments']['section']=='versions')))
            self.assertEqual(len(versions),21)
            self.assertEqual(len({v['id'] for v in versions}),21)
            source=await self.call(client,'memory_source',{'record_id':'large'})
            self.assertFalse(source['response_complete'])
            rebuilt=[]
            for start in range(0,len(long),6000):
                part=await self.call(client,'memory_source',{'record_id':'large','start':start,'end':min(start+6000,len(long))})
                rebuilt.append(part['span']['text'])
            self.assertEqual(''.join(rebuilt),long)
            raw=await self.call(client,'memory_source',{'record_id':event['id']})
            self.assertFalse(raw['response_complete'])
            raw_text=await self.collect(client,next(c for c in raw['continuations']
                if c['arguments'].get('section')=='text'))
            self.assertEqual(raw_text,long)
            tiny=await self.call(client,'memory_source',{'record_id':event['id'],'start':10,'end':30})
            self.assertTrue(tiny['response_complete'])
            self.assertFalse(tiny['source_text_complete'])
            self.assertIsNone(tiny['continuation'])
            self.assertEqual(tiny['span']['text'],long[10:30])

    async def test_route_upgrade_history_exposes_both_plans_with_historical_cutoffs(self):
        def planner(request):
            return json.dumps({'reuse_view_id':None,'related_view_ids':[],
                               'subquestions':[{'question':'Related implementation?', 'purpose':'domain_evidence'}],'reason':'Fixture upgrade.'})
        with MemoryStore(self.db) as store:
            direct=MemoryService(store).query('What was decided?',scope='routing',recorded_as_of='2026-01-15')
            upgraded=MemoryService(store,MemoryEngine(store,model=planner)).query(
                'What was decided?',scope='routing',recorded_as_of='2026-01-15')
        old_id=direct['view']['view_id']
        new_id=upgraded['view']['view_id']
        self.assertNotEqual(old_id,new_id)
        async with connect(self.db) as client:
            history=await self.call(client,'memory_history',{'view_id':new_id})
            self.assertEqual({r['view_id'] for r in history['route_revisions']},{old_id,new_id})
            route_cursor=next(c for c in history['continuations'] if c['arguments']['section']=='routes')
            routes=json.loads(await self.collect(client,route_cursor))
            self.assertEqual([r['plan']['method'] for r in routes],['direct','model'])
            self.assertTrue(all(r['plan']['request']['recorded_as_of'].startswith('2026-01-15') for r in routes))
            old=await self.call(client,'memory_history',{'view_id':old_id})
            self.assertEqual(old['versions'][0]['id'],direct['view']['id'])

    async def test_oversize_diagnostics_and_single_edge_are_explicitly_recoverable(self):
        with MemoryStore(self.db) as store:
            service=MemoryService(store)
            event=service.capture_message('s','m','fixture','user','A raw claim.',0,'fixture://s','scope')['event']
            huge='Preserve exact qualifier. '*4000
            with store.transaction(write=True) as connection:
                connection.execute('UPDATE memory_capture_jobs SET proposals=? WHERE id=?',
                    (json.dumps([{'kind':'interpretation','status':'unresolved','reason':huge}]),event['id']))
        async with connect(self.db,model='canonical') as client:
            status=await self.call(client,'memory_status',{'scope':'scope'})
            self.assertEqual(status['unresolved_count'],1)
            self.assertFalse(status['response_complete'])
            job=json.loads(await self.collect(client,next(c for c in status['continuations']
                if c['arguments'].get('section')=='job')))
            self.assertEqual(job['proposals'][0]['reason'],huge)
            query=await self.call(client,'memory_query',{'query':'claim','scope':'scope'})
            self.assertEqual(query['unresolved_count'],1)
            self.assertFalse(query['response_complete'])

    async def test_single_oversize_edge_condition_and_escaped_source_pages_remain_exact(self):
        self.seed(2)
        condition='Never omit this condition. '*4000
        escaped='\x01"\\'*6000
        with MemoryStore(self.db) as store:
            store.append_edge(edge_id='long-condition',from_id='rule-001',to_id='rule-000',kind='exception_to',
                recorded_at='2026-02-01',metadata={'condition':condition})
            event=MemoryService(store).capture_message('escaped','m','fixture','user',escaped,0,'fixture://esc','esc')['event']
        async with connect(self.db) as client:
            source=await self.call(client,'memory_source',{'record_id':'rule-001'})
            edge_cursor=next(c for c in source['continuations'] if c['arguments'].get('section')=='edges')
            with MemoryStore(self.db) as store:
                store.append_edge(edge_id='later-edge',from_id='rule-001',to_id='rule-000',kind='supports',
                                  recorded_at='2026-03-01')
            edges=json.loads(await self.collect(client,edge_cursor))
            self.assertEqual(next(e['metadata']['condition'] for e in edges if e['edge_id']=='long-condition'),condition)
            self.assertNotIn('later-edge',{e['edge_id'] for e in edges})
            cursor={'tool':'memory_source','arguments':{'record_id':event['id'],'start':100,'end':6100}}
            chunks=[]
            while cursor:
                page=await self.call(client,cursor['tool'],cursor['arguments'])
                chunks.append(page['span']['text'])
                cursor=page['continuation']
            self.assertEqual(''.join(chunks),escaped[100:6100])
            self.assertGreater(len(chunks),1)
