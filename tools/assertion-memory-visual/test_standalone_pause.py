"""Real HTTP over the Visual browser paths and a synthetic Base REST peer."""
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import test_asset_origin as fixture


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
        calls=[]
        now=[1770000000]
        ready=[True]

        class Base(BaseHTTPRequestHandler):
            def log_message(self,*_): pass

            def reply(self,status,value):
                self.send_response(status)
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def do_GET(self):
                calls.append((self.command,self.path))
                if self.path.startswith('/api/v1/queue-status?'):
                    assert self.headers['Authorization']=='Bearer '+queue
                    return self.reply(200,{'stage':lane,'release_version':'assertion-memory-v0.1.16',
                        'release_generation':'e'*64,'snapshot_at':'2026-11-25T21:00:00Z','global_order':False,
                        'queues':[],'processing_control':{'paused':state['paused'],
                        'revision':state['revision'],'can_change':ready[0]}})
                assert self.headers['Authorization']=='Bearer '+read
                if self.path=='/api/v1/queue-sessions':return self.reply(200,sessions)
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
                if self.path=='/api/v1/queue-items/source-preview':
                    assert args=={'queue_id':'source','item_id':'job','expected_revision':'1'*32}
                    return self.reply(200,{'stage':lane,'queue_id':'source','item_id':'job','source_preview':'literal <text>','source_role':'assistant'})
                if self.path=='/api/v1/queue-items/remove':
                    assert set(args)=={'queue_id','item_id','expected_revision'}
                    assert args['item_id']=='job' and args['queue_id']=='source'
                    if args['expected_revision']!='1'*32:return self.reply(409,{})
                    return self.reply(200,{'stage':lane,'queue_id':'source','item_id':'job','removed':True,'effect':'source_and_queue'})
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
