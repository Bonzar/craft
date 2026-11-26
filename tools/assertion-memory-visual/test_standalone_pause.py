"""Real HTTP over the Visual browser paths and a synthetic Base REST peer."""
import asyncio
from contextlib import nullcontext
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
from threading import Thread
import test_asset_origin as fixture
from prototypes.assertion_memory.database_clear import PATH as CLEAR_PATH
from prototypes.assertion_memory.processing_control import read_control, set_control
from prototypes.assertion_memory.queue_owner_domain import owner_operations
from prototypes.assertion_memory.queue_pause_proof import make_owner_verifier,reserve_once
from prototypes.assertion_memory.queue_pause_rest import make_queue_pause_rest
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.storage import MemoryStore


class StandalonePause(fixture.OriginTest):
    for _name in tuple(n for n in vars(fixture.OriginTest) if n.startswith('test_')):
        locals()[_name]=None

    def test_owner_pause_protocol(self):
        for lane in ('test','prod'):
            with self.subTest(lane=lane):
                self.exercise(lane)

    def exercise(self,lane):
        edge, graph, queue, read, write, actor, pause_edge = (x*64 for x in '2345678')
        secret=bytes.fromhex('9'*64)
        host=f'visual-{lane}.example.test'
        origin='https://'+host
        state=dict(stage=lane,paused=False,revision='a'*32)
        sessions=dict(stage=lane,revision='d'*32,selected_session_ids=[],sessions=[{'id':'synthetic','queued':1,'eligible':True}])
        processing=dict(stage=lane,revision='d'*32,sessions=sessions['sessions'],
            policy=dict(mode='all',included_session_ids=[],excluded_session_ids=[],added_from=None,added_through=None))
        db=(self.root/f'{lane}-disposable.sqlite3').resolve()
        with MemoryStore(db) as store:
            service=MemoryService(store)
            service.capture_message('synthetic-session','synthetic-id','fixture','user',
                                    'fictional test record',1,'fixture')
            with store.transaction(write=True) as conn:
                _,revision=read_control(conn)
                paused=set_control(conn,paused=True,expected_revision=revision)
        operations=owner_operations(database=db,release_lease=nullcontext,identity=lambda:('fixture','exact'),stage=lane)
        db_state=operations('clear_prepare',{})
        calls=[]
        now=[1770000000]
        ready=[True]
        graph_scope_mode=['match']

        def clear_commit(*,stage,expected_revision,proof):
            return operations('clear_commit',{'expected_revision':expected_revision},
                authorize=lambda conn:reserve_once(conn,stage=stage,expected_revision=expected_revision,
                    proof=proof,path=CLEAR_PATH,clock=lambda:now[0]))

        class Base(BaseHTTPRequestHandler):
            def log_message(self,*_): pass

            def reply(self,status,value):
                self.send_response(status)
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def clear_request(self,wire):
                port=self.server.server_port
                proof=make_owner_verifier(secret,actor,lane=lane,path=CLEAR_PATH,clock=lambda:now[0])
                app=make_queue_pause_rest(stage=lane,path=CLEAR_PATH,port=port,enabled=True,
                    read_sha256=hashlib.sha256(read.encode()).hexdigest(),
                    write_sha256=hashlib.sha256(write.encode()).hexdigest(),actor_sha256=actor,
                    prepare=lambda:operations('clear_prepare',{})|{'can_clear':db_state['can_clear']},
                    change=clear_commit,verify_owner=proof)
                sent=[]
                async def receive(): return {'type':'http.request','body':wire}
                async def send(event): sent.append(event)
                scope=dict(type='http',path=self.path,raw_path=self.path.encode(),query_string=b'',
                    method=self.command,client=('127.0.0.1',self.client_address[1]),server=('127.0.0.1',port),
                    headers=[(k.lower().encode(),v.encode()) for k,v in self.headers.items()])
                asyncio.run(app(scope,receive,send))
                return self.reply(sent[0]['status'],json.loads(sent[1]['body']))

            def do_GET(self):
                calls.append((self.command,self.path))
                if self.path=='/api/v1/capabilities?database_clear_scope=1':
                    assert self.headers['Authorization']=='Bearer '+graph
                    if graph_scope_mode[0]=='old':
                        return self.reply(200,{'capabilities':{'live':True}})
                    scope=operations('clear_prepare',{})['scope']
                    return self.reply(200,{'stage':lane,'database_clear_scope':scope if graph_scope_mode[0]=='match' else '0'*64})
                if self.path.startswith('/api/v1/queue-status?'):
                    assert self.headers['Authorization']=='Bearer '+queue
                    return self.reply(200,{'stage':lane,'release_version':'assertion-memory-v0.1.16',
                        'release_generation':'e'*64,'snapshot_at':'2026-11-25T21:00:00Z','global_order':False,
                        'queues':[],**({'database_clear':{k:db_state[k] for k in ('revision','can_clear','scope')}}
                            if self.headers.get('X-Memory-Queue-Reset')=='ready-v1' else {}),
                        'processing_control':{'paused':state['paused'],
                        'revision':state['revision'],'can_change':ready[0]}})
                assert self.headers['Authorization']=='Bearer '+read
                if self.path=='/api/v1/queue-sessions':return self.reply(200,sessions)
                if self.path=='/api/v1/queue-processing-policy':return self.reply(200,processing)
                if self.path==CLEAR_PATH:return self.clear_request(b'')
                assert self.path=='/api/v1/queue-pause'
                self.reply(200,state)

            def do_PUT(self):
                calls.append((self.command,self.path))
                assert self.headers['Authorization']=='Bearer '+write
                assert not self.headers['Cookie'] and not self.headers['Origin']
                assert not self.headers['X-CSRF-Token']
                assert not self.headers['X-Memory-Visual-Owner-Ingress']
                assert not self.headers['X-Memory-Visual-Pause-Owner-Ingress']
                wire=self.rfile.read(int(self.headers['Content-Length']))
                args=json.loads(wire)
                nonce=self.headers['X-Memory-Pause-Nonce']
                mac='\n'.join(('memory-queue-pause-v1','PUT',self.path,lane,
                    hashlib.sha256(wire).hexdigest(),actor,args['expected_revision'],str(now[0]),
                    str(now[0]+15),nonce))+'\n'
                assert hmac.compare_digest(self.headers['X-Memory-Pause-Signature'],
                     hmac.digest(secret,mac.encode('ascii'),'sha256').hex())
                if self.path==CLEAR_PATH:
                    assert set(args)=={'expected_revision'}
                    return self.clear_request(wire)
                if self.path=='/api/v1/queue-items/source-preview':
                    assert args=={'queue_id':'source','item_id':'job','expected_revision':'1'*32}
                    return self.reply(200,{'stage':lane,'queue_id':'source','item_id':'job','source_preview':'literal <text>','source_role':'assistant'})
                if self.path=='/api/v1/queue-items/remove':
                    assert set(args)=={'queue_id','item_id','expected_revision'}
                    assert args['item_id']=='job' and args['queue_id']=='source'
                    if args['expected_revision']!='1'*32:return self.reply(409,{})
                    return self.reply(200,{'stage':lane,'queue_id':'source','item_id':'job','removed':True,'effect':'source_and_queue'})
                if self.path=='/api/v1/queue-processing-policy':
                    assert set(args)=={'policy','expected_revision'}
                    if args['expected_revision']!=processing['revision']:return self.reply(409,{})
                    p=args['policy'].copy()
                    p['added_through']='2026-11-26T23:59:59.999999+00:00'
                    processing.update(revision='f'*32,policy=p)
                    return self.reply(200,processing)
                if self.path=='/api/v1/queue-sessions':
                    assert set(args)=={'selected_session_ids','expected_revision'}
                    if args['expected_revision']!=sessions['revision']:return self.reply(409,{})
                    sessions.update(revision='e'*32,selected_session_ids=args['selected_session_ids'])
                    return self.reply(200,sessions)
                assert set(args)=={'paused','expected_revision'}
                if args['expected_revision']!=state['revision']:
                    return self.reply(409,{})
                state.update(paused=args['paused'],revision=('b' if args['paused'] else 'c')*32)
                self.reply(200,state)

        def start(http):
            Thread(target=http.serve_forever,daemon=True).start()
            self.addCleanup(http.server_close)
            self.addCleanup(http.shutdown)
            return http

        base=start(ThreadingHTTPServer(('127.0.0.1',0),Base))
        url=f'http://127.0.0.1:{base.server_port}'
        opts=dict(queue_origin=url,queue_token=queue,standalone=(origin,edge,url,graph))
        visual=start(fixture.origin.make_server(self.root,fixture.TOKEN,0,environment=lane,**opts,
                    pause=(url,read,write,b'c'*32,pause_edge,actor,secret),clock=lambda:now[0]))

        def request(method, path, data, *, trusted=edge, proof=pause_edge, sent_origin=origin, extra=None):
            conn=http.client.HTTPConnection(*visual.server_address,timeout=2)
            headers={'Host':host,'Origin':sent_origin,'Content-Type':'application/json',
                     'Cookie':'ordinary=browser',**(extra or {})}
            if trusted:
                headers['X-Memory-Visual-Owner-Ingress']=trusted
            if proof:
                headers['X-Memory-Visual-Pause-Owner-Ingress']=proof
            try:
                conn.request(method,path,None if data is None else json.dumps(data),headers=headers)
                r=conn.getresponse()
                raw=r.read()
                return r.status,json.loads(raw) if raw else None
            finally:
                conn.close()

        path='/api/v1/processing/pause'
        self.assertEqual(request('POST',path+'/prepare',{},trusted=None,
                                 extra={'OAI-Authenticated-User-Id':'owner'})[0],403)
        self.assertEqual(request('POST',path+'/prepare',{},sent_origin='https://foreign.example.test')[0],403)
        self.assertEqual(request('POST',path+'/prepare?fixture=1',{})[0],404)
        self.assertEqual(request('POST',path+'/prepare',{},extra={'Sec-Fetch-Site':'cross-site'})[0],403)
        self.assertEqual(request('POST',path+'/prepare',{},proof=None)[0],403)
        self.assertEqual(calls,[])
        policy_path='/api/v1/processing/sessions'
        code,old_session=request('POST',policy_path+'/prepare',{})
        self.assertEqual(code,200)
        code,prepared=request('POST',policy_path+'/prepare',{'format':'policy-v1'})
        self.assertEqual(code,200)
        self.assertEqual(prepared['policy']['mode'],'all')
        self.assertNotEqual(old_session['csrf_token'],prepared['csrf_token'])
        command=dict(stage=lane,expected_revision=prepared['revision'],policy=dict(
            mode='all',included_session_ids=[],excluded_session_ids=['synthetic'],
            added_from=None,added_through='2026-11-26T23:59:59.999999Z'))
        self.assertEqual(request('PUT',policy_path,command,extra={'X-CSRF-Token':old_session['csrf_token']})[0],403)
        token={'X-CSRF-Token':prepared['csrf_token']}
        self.assertEqual(request('PUT',policy_path,{**command,'stage':'prod' if lane=='test' else 'test'},extra=token)[0],409)
        code,reply=request('PUT',policy_path,command,extra=token)
        self.assertEqual(code,200)
        self.assertEqual(reply['policy']['excluded_session_ids'],['synthetic'])
        self.assertEqual(reply['policy']['added_through'],'2026-11-26T23:59:59.999999+00:00')
        self.assertEqual(request('PUT',policy_path,command,extra=token)[0],409)
        calls.clear()
        status_path='/api/v1/capabilities?queue_status=1'
        for base_ready,marker in [(True,pause_edge),(False,pause_edge),(True,None)]:
            ready[0]=base_ready
            code,view=request('GET',status_path,None,proof=marker)
            self.assertEqual(code,200)
            self.assertIs(view['processing_control']['can_change'],False)
            self.assertEqual('standalone_pause' in view['capabilities'],bool(base_ready and marker))
        calls.clear()
        code,prepare=request('POST',path+'/prepare',{})
        self.assertEqual(code,200)
        self.assertEqual(set(prepare),{'stage','paused','revision','csrf_token'})
        before=dict(stage=lane,paused=True,expected_revision=prepare['revision'])
        token={'X-CSRF-Token':prepare['csrf_token']}
        self.assertEqual(request('PUT',path,before)[0],403)
        self.assertEqual(request('PUT',path,{**before,'stage':'prod' if lane=='test' else 'test'},extra=token)[0],409)
        self.assertEqual(calls,[('GET','/api/v1/queue-pause')])
        self.assertEqual(request('PUT',path,before,extra=token),(200,{**state,'paused':True,'revision':'b'*32}))
        self.assertEqual(request('PUT',path,before,extra=token)[0],409)
        code,prepare=request('POST',path+'/prepare',{})
        self.assertEqual(code,200)
        before=dict(stage=lane,paused=False,expected_revision=prepare['revision'])
        token={'X-CSRF-Token':prepare['csrf_token']}
        now[0]+=31
        self.assertEqual(request('PUT',path,before,extra=token)[0],403)
        self.assertEqual(sum(m=='PUT' for m,p in calls),2)
        _,prepare=request('POST',path+'/prepare',{})
        self.assertEqual(request('PUT',path,before,extra={'X-CSRF-Token':prepare['csrf_token']})[0],200)
        self.assertFalse(state['paused'])
        self.assertEqual({path for _,path in calls},{'/api/v1/queue-pause'})
        calls.clear()
        select='/api/v1/processing/sessions'
        self.assertEqual(request('POST',select+'/prepare',{},proof=None)[0],403)
        self.assertEqual(request('POST',select+'/prepare',{},sent_origin='https://wrong.example.test')[0],403)
        self.assertEqual(calls,[])
        code,view=request('POST',select+'/prepare',{})
        self.assertEqual(code,200)
        body=dict(stage=lane,selected_session_ids=['synthetic'],expected_revision=view['revision'])
        self.assertEqual(request('PUT',select,body)[0],403)
        other='prod' if lane=='test' else 'test'
        self.assertEqual(request('PUT',select,{**body,'stage':other},extra={'X-CSRF-Token':view['csrf_token']})[0],409)
        cross=fixture.origin.hmac.digest(b'c'*32,f'visual-pause-v1\ntest\n{origin}\n{view["revision"]}\n{now[0]+30}'.encode(),'sha256').hex()
        self.assertEqual(request('PUT',select,body,extra={'X-CSRF-Token':f'{now[0]+30}.'+cross})[0],403)
        self.assertEqual(request('PUT',select,body,extra={'X-CSRF-Token':view['csrf_token']})[0],200)
        self.assertEqual(sessions['selected_session_ids'],['synthetic'])
        self.assertEqual(request('PUT',select,body,extra={'X-CSRF-Token':view['csrf_token']})[0],409)
        self.assertTrue(all(p=='/api/v1/queue-sessions' for _,p in calls))
        calls.clear()
        delete='/api/v1/processing/items/remove'
        body={'stage':lane,'queue_id':'source','item_id':'job','expected_revision':'1'*32}
        self.assertEqual(request('POST',delete+'/prepare',body,proof=None)[0],403)
        code,proof=request('POST',delete+'/prepare',body)
        self.assertEqual(code,200)
        token={'X-CSRF-Token':proof['csrf_token']}
        self.assertEqual(request('PUT',delete,{**body,'item_id':'other'},extra=token)[0],403)
        self.assertEqual(request('PUT',delete,body)[0],403)
        self.assertEqual(calls,[])
        self.assertEqual(request('PUT',delete,body,extra=token),(200,{'stage':lane,'queue_id':'source','item_id':'job','removed':True,'effect':'source_and_queue'}))
        stale={**body,'expected_revision':'2'*32}
        _,proof=request('POST',delete+'/prepare',stale)
        self.assertEqual(request('PUT',delete,stale,extra={'X-CSRF-Token':proof['csrf_token']})[0],409)
        self.assertTrue(all(p=='/api/v1/queue-items/remove' for _,p in calls))
        calls.clear()
        preview='/api/v1/processing/items/source-preview'
        _,proof=request('POST',preview+'/prepare',body)
        self.assertEqual(request('PUT',delete,body,extra={'X-CSRF-Token':proof['csrf_token']})[0],403)
        self.assertEqual(request('PUT',preview,body,extra=token)[0],403)
        self.assertEqual(calls,[])
        self.assertEqual(request('PUT',preview,body,extra={'X-CSRF-Token':proof['csrf_token']}),
            (200,{'stage':lane,'queue_id':'source','item_id':'job','source_preview':'literal <text>','source_role':'assistant'}))
        self.assertEqual(calls,[('PUT','/api/v1/queue-items/source-preview')])
        calls.clear()
        clear='/api/v1/processing/database/clear'
        snapshot='/api/v1/capabilities?queue_status=1'
        self.assertNotIn('database_clear',request('GET',snapshot,None,proof=None)[1])
        self.assertEqual(request('GET',snapshot,None)[1]['database_clear'],{'revision':db_state['revision'],'can_clear':True})
        for mode in ('wrong','old'):
            graph_scope_mode[0]=mode
            code,payload=request('GET',snapshot,None)
            self.assertEqual(code,200)
            self.assertNotIn('database_clear',payload)
        graph_scope_mode[0]='match'
        self.assertTrue(all(p.startswith('/api/v1/queue-status?') or p=='/api/v1/capabilities?database_clear_scope=1' for _,p in calls))
        self.assertEqual(request('GET','/api/v1/capabilities?database_clear_scope=1',None)[0],404)
        calls.clear()
        body={'stage':lane,'expected_revision':db_state['revision']}
        prepare={**body,'confirmation':'ОЧИСТИТЬ '+lane.upper()}
        self.assertEqual(request('GET',clear,None)[0],405)
        self.assertEqual(request('POST',clear+'/prepare',prepare,proof=None)[0],403)
        self.assertEqual(request('POST',clear+'/prepare',prepare,sent_origin='https://other.example.test')[0],403)
        self.assertEqual(request('POST',clear+'/prepare?anything=1',prepare)[0],404)
        self.assertEqual(request('POST',clear+'/prepare',{**prepare,'confirmation':'ОЧИСТИТЬ '+other.upper()})[0],400)
        self.assertEqual(request('POST',clear+'/prepare',{**prepare,'stage':other})[0],409)
        self.assertEqual(request('PUT',clear,body)[0],403)
        self.assertEqual(calls,[])
        db_state['can_clear']=False
        self.assertEqual(request('POST',clear+'/prepare',prepare)[0],409)
        db_state['can_clear']=True
        graph_scope_mode[0]='wrong'
        self.assertEqual(request('POST',clear+'/prepare',prepare)[0],409)
        graph_scope_mode[0]='match'
        self.assertEqual(request('POST',clear+'/prepare',{**prepare,'expected_revision':'8'*32})[0],409)
        code,prepared=request('POST',clear+'/prepare',prepare)
        self.assertEqual(code,200)
        self.assertEqual(prepared['revision'],body['expected_revision'])
        self.assertTrue(all(m=='GET' and p in (CLEAR_PATH,'/api/v1/capabilities?database_clear_scope=1') for m,p in calls))
        calls.clear()
        token={'X-CSRF-Token':prepared['csrf_token']}
        self.assertEqual(request('PUT',clear,{**body,'stage':other},extra=token)[0],409)
        self.assertEqual(request('PUT',clear,{**body,'expected_revision':'8'*32},extra=token)[0],403)
        self.assertEqual(calls,[])
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],1)
        code,cleared=request('PUT',clear,body,extra=token)
        self.assertEqual(code,200)
        self.assertEqual(cleared,{'stage':lane,'revision':operations('clear_prepare',{})['revision'],'cleared':True})
        self.assertNotEqual(cleared['revision'],body['expected_revision'])
        with sqlite3.connect(db) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM memory_raw_events').fetchone()[0],0)
            self.assertEqual(read_control(conn),paused)
        self.assertEqual(request('PUT',clear,body,extra=token)[0],409)
        self.assertEqual([x for x in calls if x[0]=='PUT'],[('PUT',CLEAR_PATH)])
