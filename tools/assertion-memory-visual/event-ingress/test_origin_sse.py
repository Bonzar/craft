from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from unittest import main
import test_asset_origin as fixture

class Stream(fixture.OriginTest):
    for _name in tuple(n for n in vars(fixture.OriginTest) if n.startswith("test_")):
        locals()[_name]=None
    def test_two_flushes_before_upstream_closes_and_auth_is_preserved(self):
        for lane in ('test','prod'):
            with self.subTest(lane=lane):
                self.exercise_stream(lane)

    def exercise_stream(self,lane):
        sent=[]
        first_read, second_read=Event(), Event()
        key='3'*64
        ingress='2'*64
        host=f'visual-{lane}.example.test'
        class Graph(BaseHTTPRequestHandler):
            def log_message(self,*_): pass
            def do_GET(self):
                sent.append((self.path,self.headers.get('Authorization'),
                             self.headers.get('Last-Event-ID'),self.headers.get('Cookie')))
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.end_headers()
                for frame,read in ((b'event: hello\ndata: {}\n\n',first_read),
                                   (b'event: heartbeat\ndata: {}\n\n',second_read)):
                    self.wfile.write(frame)
                    self.wfile.flush()
                    if not read.wait(3): break
        def start(server):
            Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            return server
        upstream=start(ThreadingHTTPServer(('127.0.0.1',0),Graph))
        visual=start(fixture.origin.make_server(self.root,fixture.TOKEN,0,environment=lane,
                       queue_origin='http://127.0.0.1:'+str(upstream.server_port),queue_token='4'*64,standalone=('https://'+host,ingress,'http://127.0.0.1:'+str(upstream.server_port),key)))
        path='/api/v1/events?after=10'
        for authorized in (False,True):
            conn=HTTPConnection(*visual.server_address,timeout=2)
            try:
                headers={'Host':host,'Last-Event-ID':'11','Cookie':'fixture=owner'}
                if authorized: headers['X-Memory-Visual-Owner-Ingress']=ingress
                conn.request('GET',path,headers=headers)
                response=conn.getresponse()
                self.assertEqual(response.status,200 if authorized else 403)
                if not authorized:
                    self.assertEqual(sent,[])
                    continue
                self.assertEqual(response.headers.get('Content-Type'),'text/event-stream')
                self.assertEqual(response.headers.get('X-Accel-Buffering'),'no')
                for frame,read in ((b'event: hello\ndata: {}\n\n',first_read),
                                   (b'event: heartbeat\ndata: {}\n\n',second_read)):
                    self.assertEqual(response.read(len(frame)),frame)
                    read.set()
                self.assertEqual(sent,[(path,'Bearer '+key,'11',None)])
            finally:
                if authorized:
                    first_read.set()
                    second_read.set()
                conn.close()

if __name__=='__main__':
    main()
