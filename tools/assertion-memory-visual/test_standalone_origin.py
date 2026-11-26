import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from unittest import main
import test_asset_origin as fixture


class Standalone(fixture.OriginTest):
    # Reuse setup/helper only; don't rerun inherited tests.
    for _name in tuple(n for n in vars(fixture.OriginTest) if n.startswith('test_')):
        locals()[_name]=None

    def test_real_standalone_browser_entry_on_both_lanes(self):
        for lane in ('test','prod'):
            with self.subTest(lane=lane):
                self.exercise(lane)

    def test_stable_entry_rejects_unknown_assets(self):
        with self.assertRaisesRegex(ValueError,'Invalid standalone entry asset'):
            fixture.origin.standalone_entry_html(
                fixture.ORIGINAL['index.html'].replace(b'index-ab.js',b'index-other.js'),
                self.release,fixture.ORIGINAL)

    def exercise(self,lane):
        ingress,graph_key,queue_key,health_key=(x*64 for x in ('2','3','4','7'))
        host=f'visual-{lane}.example.test'
        captured=[]
        snapshot={'stage':lane, 'release_version':'assertion-memory-v0.1.16',
                  'release_generation':'5'*64,'global_order':False,
                  'snapshot_at':'2026-11-25T20:00:00+00:00','queues':[],
                  'processing_control':{'paused':False,'revision':'6'*32,'can_change':False}}

        class PrivateReader(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                captured.append((self.path,dict(self.headers.items())))
                if self.path.startswith('/api/v1/events?'):
                    self.send_response(200)
                    self.send_header('Content-Type','text/event-stream')
                    self.end_headers()
                    self.wfile.write(b'event: queue_changed\n\nevent: graph_changed\n\n')
                    return
                if self.path.startswith('/api/v1/queue-status?'):
                    payload=json.dumps(snapshot).encode()
                else:
                    payload=b'{"stage":"'+lane.encode()+b'","fixture":"graph"}'
                self.send_response(200)
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(payload)

        def start(http):
            Thread(target=http.serve_forever,daemon=True).start()
            self.addCleanup(http.server_close)
            self.addCleanup(http.shutdown)
            return http

        private=start(ThreadingHTTPServer(('127.0.0.1',0),PrivateReader))
        url=f'http://127.0.0.1:{private.server_port}'
        visual=start(fixture.origin.make_server(self.root,fixture.TOKEN,0,environment=lane,
                     queue_origin=url,queue_token=queue_key,standalone=('https://'+host,ingress,url,graph_key),
                     runtime_health=('8'*64,health_key)))

        def get(path, *, key=ingress, sent_host=host, method='GET', extra=None):
            conn=http.client.HTTPConnection(*visual.server_address,timeout=2)
            headers={'Host':sent_host,**(extra or {})}
            if key:
                headers['X-Memory-Visual-Owner-Ingress']=key
            try:
                conn.request(method,path,headers=headers)
                rsp=conn.getresponse()
                return rsp.status,dict(rsp.getheaders()),rsp.read()
            finally:
                conn.close()

        self.assertEqual(get('/',key=None,extra={'oai-authenticated-user-id':'owner'})[0],403)
        self.assertEqual(get('/',sent_host='spoof.example')[0],421)
        self.assertEqual(get('/api/v1/graph',extra={'Origin':'https://unauthorized.example'})[0],403)
        self.assertEqual(get('/api/v1/processing/pause',method='PUT')[0],405)
        self.assertEqual(get('/api/v1/capabilities?queue_status=1&limit=51')[0],400)
        expected=fixture.ORIGINAL['index.html'].replace(
            b'./assets/',('/visual/'+self.release+'/assets/').encode('ascii'))
        for path in ('/?focus=fixture','/visual','/visual/?mode=current'):
            code,headers,page=get(path)
            self.assertEqual(code,200)
            self.assertNotIn('Location',headers)
            self.assertIn("base-uri 'none'",headers['Content-Security-Policy'])
            self.assertEqual(page,expected)
        code,headers,page=get('/visual',method='HEAD')
        self.assertEqual((code,page,int(headers['Content-Length'])),(200,b'',len(expected)))
        self.assertEqual(get('/visual/'+self.release+'/index.html')[2],fixture.ORIGINAL['index.html'])
        self.assertEqual(get('/visual/'+self.release+'/assets/index-ab.js')[2],fixture.ORIGINAL['assets/index-ab.js'])
        stale=dict(fixture.ORIGINAL, **{'index.html':fixture.ORIGINAL['index.html'].replace(b'synthetic',b'other retained version')})
        stale_manifest=(json.dumps({k:fixture.origin.digest(v) for k,v in sorted(stale.items())},
                                    separators=(',',':'))+'\n').encode()
        stale_release=fixture.origin.digest(stale_manifest)
        retained=self.root/'releases'/stale_release
        (retained/'assets').mkdir(parents=True,exist_ok=True)
        (retained/'manifest.json').write_bytes(stale_manifest)
        for k,v in stale.items():
            (retained/k).write_bytes(v)
        for path in ('/visual/'+stale_release+'/', '/visual/'+stale_release+'/index.html?discarded=1'):
            status,headers,body=get(path)
            self.assertEqual((status,headers.get('Location'),body),(307,'/visual',b''))
            self.assertEqual(get(path,key=None)[0],403)
        self.assertEqual(get('/visual/'+stale_release+'/assets/index-ab.js')[0],404)
        # The legacy transport never opens a retired release either.
        conn=http.client.HTTPConnection(*visual.server_address,timeout=2)
        try:
            conn.request('POST',f'/memory-visual-{lane}-read',
                         body=json.dumps({'op':'asset','digest':stale_release,'name':'index.html'}),
                         headers={'Authorization':'Bearer '+fixture.TOKEN,'Content-Type':'application/json'})
            rsp=conn.getresponse()
            self.assertEqual((rsp.status,rsp.read()),(404,b''))
        finally:
            conn.close()
        link=self.root/'current'
        next_link=self.root/'switch-fixture'
        next_link.symlink_to('releases/'+stale_release)
        next_link.replace(link)
        try:
            code,h,body=get('/visual/'+self.release+'/',method='HEAD')
            self.assertEqual((code,h.get('Location'),body),(307,'/visual',b''))
            for name in ('assets/index-ab.js','assets/index-ab.css','assets/graph.worker-ef.js'):
                code,h,body=get('/visual/'+self.release+'/'+name)
                self.assertEqual((code,h.get('Location'),body),(404,None,b''))
            self.assertEqual(get('/visual/'+stale_release+'/')[2],stale['index.html'])
            status,headers,page=get('/visual?marker=kept')
            self.assertEqual(status,200)
            self.assertNotIn('Location',headers)
            self.assertEqual(page,stale['index.html'].replace(
                b'./assets/',('/visual/'+stale_release+'/assets/').encode('ascii')))
        finally:
            next_link.symlink_to('releases/'+self.release)
            next_link.replace(link)

        self.assertEqual(json.loads(get('/api/v1/session')[2]),{'authenticated':True})
        self.assertEqual(captured,[])
        self.assertEqual(json.loads(get('/api/v1/graph?selected_date=2026-11-25',
                         extra={'Authorization':'Bearer browser','Cookie':'private=browser'})[2])['fixture'],'graph')
        self.assertEqual(json.loads(get('/api/v1/capabilities?queue_status=1&offset=2')[2])['stage'],lane)
        [(path,g),(queue,q)]=captured
        self.assertEqual(path,'/api/v1/graph?selected_date=2026-11-25')
        self.assertEqual(queue,'/api/v1/queue-status?limit=50&offset=2')
        self.assertEqual(g['Authorization'],'Bearer '+graph_key)
        self.assertEqual(q['Authorization'],'Bearer '+queue_key)
        self.assertFalse(any(k.lower() in ('cookie','x-memory-visual-owner-ingress') for k in g))
        self.assertEqual(get('/api/v1/events?channel=dashboard')[2],
                         b'event: queue_changed\n\nevent: graph_changed\n\n')
        self.assertEqual(get('/__visual-runtime-health')[0],404)
        probe=http.client.HTTPConnection(*visual.server_address,timeout=3)
        try:
            probe.request('GET','/__visual-runtime-health',headers={'Authorization':'Bearer '+health_key})
            rsp=probe.getresponse()
            self.assertEqual(rsp.status,200)
            self.assertEqual(json.loads(rsp.read()),{'stage':lane,'runtime_entry_sha':'8'*64,
                'static_digest':self.release,'queue_ready':True})
        finally:
            probe.close()
        snapshot['stage']='prod' if lane=='test' else 'test'
        self.assertEqual(get('/api/v1/capabilities?queue_status=1')[0],503)


if __name__=='__main__':
    main()
