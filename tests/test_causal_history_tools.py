"""Public causal-history wrappers; local synthetic data and no provider calls."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.mcp_server import ServiceConfig,_register_read_tools,_query_with_connection,create_server

class FakeMCP:
    def __init__(self):self.tools={}
    def tool(self):
        def register(fn):self.tools[fn.__name__]=fn;return fn
        return register

class CausalHistoryToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.config=ServiceConfig(str(Path(self.tmp.name)/'memory.sqlite3'),'none')
        self.store=MemoryStore(self.config.db);self.addCleanup(self.store.close)
        self.service=MemoryService(self.store,self.config.engine(self.store))
        self.service.capture_message('native-chat','m1','fixture','user','Synthetic reported condition.',0,'fixture://one','s')
        self.store.append_assertion(assertion_id='fixture',text='Synthetic reported condition.',actor='fixture',source='fixture://one',episode='native-chat',scope='s',recorded_at='2026-01-01')
        self.mcp=FakeMCP();_register_read_tools(self.mcp,self.service,self.config)
    def test_three_readonly_public_tools_and_exact_query_output_pin(self):
        answer=asyncio.run(self.mcp.tools['memory_query']('Synthetic condition?',scope='s',request_session_id='caller-chat'))
        page=asyncio.run(self.mcp.tools['memory_session_outputs']('caller-chat'))
        self.assertEqual(len(page['entries']),1);entry=page['entries'][0]
        self.assertEqual(entry['version_id'],answer['view']['id']);self.assertEqual(entry['check_id'],answer['freshness_check']['id'])
        self.assertEqual(entry['run_kind'],'direct_request');self.assertEqual(entry['cause_status'],'caller_context_only')
        before=self.store._connection.total_changes;self.store._connection.execute('PRAGMA query_only=ON')
        run=asyncio.run(self.mcp.tools['memory_run_history'](entry['run_id'],section='run'))
        event=asyncio.run(self.mcp.tools['memory_run_event'](entry['event_id']))
        self.assertEqual(json.loads(run['content'])['frontier']['request_session_id'],'caller-chat')
        self.assertEqual(json.loads(event['content'])['version_id'],entry['version_id'])
        self.assertEqual(self.store._connection.total_changes,before)
    def test_cached_fastpath_records_reused_once_without_publish(self):
        first=self.service.query('Synthetic condition?',scope='s',request_session_id='first-client')
        from prototypes.assertion_memory.query_snapshot import receipt
        view=self.service.engine.get_version(first['view']['id'])
        check=receipt(self.service.engine,first['freshness_check']['id'])
        # Exercise the existing applicability fastpath using actual saved pins.
        # The synthetic stub grants no factual acceptance; it tests caller delivery.
        route=copy.deepcopy(self.service.engine.prepare_question('Synthetic condition?',scopes=('s',)))
        route['plan']['request']['question']='Equivalent synthetic question?'
        with patch.object(self.service.engine,'prepare_question',return_value=route), \
             patch('prototypes.assertion_memory.answer_applicability.attempt',return_value=(view,check)), \
             patch.object(self.service.engine,'_publish',side_effect=AssertionError('cached read must not republish')):
            second=self.service.query('Equivalent synthetic question?',scope='s',request_session_id='second-client')
        self.assertEqual(first['view']['id'],second['view']['id'])
        entries=self.service.session_outputs('second-client')['entries']
        self.assertEqual(len(entries),1);self.assertEqual(entries[0]['outcome'],'reused')
        self.assertEqual(entries[0]['check_id'],second['freshness_check']['id'])
    def test_old_query_without_context_does_not_invent_caller(self):
        self.service.query('Synthetic condition?',scope='s')
        self.assertEqual(self.service.session_outputs('native-chat')['entries'],[])
        self.assertEqual(self.store._connection.execute('SELECT count(*) FROM memory_run_provenance').fetchone()[0],0)
    def test_invalid_context_and_query_validate_before_mutations(self):
        before=self.store._connection.total_changes
        for value in ('', ' ', 'x'*2049,True):
            with self.assertRaises(ValueError):self.service.query('Synthetic?',request_session_id=value)
        with self.assertRaises(ValueError):self.service.query('',request_session_id='client')
        with self.assertRaises(ValueError):self.service.query('Synthetic?',recorded_as_of='not-a-date',request_session_id='client')
        self.assertEqual(self.store._connection.total_changes,before)
    def test_reads_have_bounds_and_frozen_event_watermark(self):
        self.service.query('Synthetic condition?',scope='s',request_session_id='client')
        first=self.service.session_outputs('client',limit=1)
        self.service.query('Synthetic condition?',scope='s',request_session_id='client')
        pinned=self.service.session_outputs('client',snapshot_id=first['snapshot_id'])
        self.assertEqual(pinned['total'],first['total'])
        entry=first['entries'][0]
        for kwargs in ({'limit':101},{'offset':-1},{'snapshot_id':-1}):
            with self.assertRaises(ValueError):self.service.session_outputs('client',**kwargs)
        with self.assertRaises(ValueError):self.service.run_history(entry['run_id'],section='bad')
        with self.assertRaises(ValueError):self.service.run_event(entry['event_id'],limit=6001)
    def test_real_sdk_registers_names_and_optional_query_context_schema(self):
        server=create_server(self.config,background=False,store=self.store)
        listed={tool.name:tool for tool in asyncio.run(server.list_tools())}
        for name in ('memory_session_outputs','memory_run_history','memory_run_event'):
            self.assertIn(name,listed)
        query=listed['memory_query'].inputSchema
        self.assertIn('request_session_id',query['properties'])
        self.assertNotIn('request_session_id',query.get('required',[]))
        self.assertEqual(listed['memory_run_event'].inputSchema['properties']['event_id']['type'],'integer')
    def test_authenticated_http_public_query_and_causal_read_tools(self):
        import httpx
        from prototypes.assertion_memory.http_server import create_http_app
        async def scenario():
            token='synthetic-causal-wire-'+'x'*40
            app=create_http_app(self.config,token,'causal.test:9443',background=False)
            async with app.lifespan(),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://causal.test:9443',
                headers={'Authorization':'Bearer '+token,'Accept':'application/json, text/event-stream'}) as client:
                async def call(name,arguments):
                    response=await client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':arguments}})
                    self.assertEqual(response.status_code,200)
                    value=response.json()['result'];self.assertFalse(value.get('isError',False))
                    return value.get('structuredContent') or json.loads(value['content'][0]['text'])
                answer=await call('memory_query',{'query':'Synthetic condition?','scope':'s','request_session_id':'http-caller'})
                outputs=await call('memory_session_outputs',{'session_id':'http-caller'})
                entry=outputs['entries'][0]
                self.assertEqual(entry['version_id'],answer['view']['id'])
                self.assertEqual(entry['check_id'],answer['freshness_check']['id'])
                detail=await call('memory_run_event',{'event_id':entry['event_id']})
                run=await call('memory_run_history',{'run_id':entry['run_id'],'section':'run'})
                self.assertEqual(json.loads(detail['content'])['version_id'],entry['version_id'])
                self.assertEqual(json.loads(run['content'])['cause_status'],'caller_context_only')
        asyncio.run(scenario())
    def test_thread_fresh_connection_forwards_optional_caller_context(self):
        answer=_query_with_connection(self.config,'Synthetic condition?','s',None,None,request_session_id='thread-client')
        entries=self.service.session_outputs('thread-client')['entries']
        self.assertEqual(entries[-1]['version_id'],answer['view']['id'])
        self.assertEqual(entries[-1]['check_id'],answer['freshness_check']['id'])

if __name__=='__main__':unittest.main()
