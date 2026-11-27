"""Real collector, dummy credentials, ephemeral loopback only."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Thread
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Queue
from time import monotonic, sleep, time_ns

ROOT=Path(__file__).resolve().parent
DUMMY={'MONIUM_TRACES_PROJECT':'synthetic-project','MONIUM_TRACES_APPLICATION_KEY':'synthetic-key'}
SECRET='FICTIONAL_PAYLOAD_NOT_FOR_EXPORT'
QUERY_STAGES=('query_response_wait','ready_result_persist','answer_persist',
              'answer_check_write','model_step_replay')


def attr(key,value):
    return {'key':key,'value':{'stringValue':value}}


def resource(lane, specs, *, scope='assertion_memory.query_timing', service=None):
    start=str(time_ns())
    spans=[]
    for serial,name,linked in specs:
        span={'traceId':f'{serial:032x}','spanId':f'{serial:016x}','name':name,'kind':3,
              'startTimeUnixNano':start,'endTimeUnixNano':start,
              'traceState':'fixture='+SECRET,'status':{'code':2,'message':SECRET},'events':[{'name':SECRET,'timeUnixNano':start}],
              'attributes':[attr('gen_ai.operation.name','chat'),attr('gen_ai.input.messages',SECRET),
                            attr('memory.stage','provider_attempt'),attr('unknown',SECRET)]}
        if serial==8:
            span['attributes'].append({'key':'memory.retry_count','value':{'intValue':'2'}})
        else:
            span['attributes'].append(attr('memory.retry_count',SECRET))
        if linked:
            span['links']=[{'traceId':'2'*32,'spanId':'2'*16,'attributes':[attr('unknown',SECRET)]}]
        spans.append(span)
    return {'resource':{'attributes':[attr('service.name',service or 'assertion-memory-'+lane),
                attr('deployment.environment.name',lane),attr('unknown',SECRET)]},
            'scopeSpans':[{'scope':{'name':scope,'version':SECRET,
                            'attributes':[attr('unknown',SECRET)]},'spans':spans}]}


def fixture(lane):
    opposite='prod' if lane=='test' else 'test'
    return {'resourceSpans':[
        resource(lane,[(1,'openai.chat',False),(2,SECRET,False),(3,'openai.chat',True),
                       (8,'memory_query.result_persist',False),
                       *((serial,'memory_query.'+stage,False)
                         for serial,stage in enumerate(QUERY_STAGES,9))]),
        resource(opposite,[(4,'openai.chat',False)]),
        resource(lane,[(5,'openai.chat',False)],scope='foreign'),
        resource(opposite,[(6,'openai.chat',False)],service='assertion-memory-'+lane),
        resource(lane,[(7,'openai.chat',False)],service='assertion-memory-'+opposite)]}


def smoke(binary,lane):
    env={**os.environ,**DUMMY,'NO_PROXY':'127.0.0.1','no_proxy':'127.0.0.1'}
    configs=['--config='+str(ROOT/'common.yaml'),'--config='+str(ROOT/(lane+'.yaml'))]
    subprocess.run([binary,'validate',*configs],env=env,check=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    exports=Queue()
    class Sink(BaseHTTPRequestHandler):
        def do_POST(self):
            packet=self.rfile.read(int(self.headers['Content-Length']))
            exports.put((self.path,{k.lower():v for k,v in self.headers.items()},json.loads(packet)))
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.end_headers()
            self.wfile.write(b'{}')
        def log_message(self,*_args):
            pass
    with tempfile.TemporaryDirectory() as local, HTTPServer(('127.0.0.1',0),Sink) as sink:
        Thread(target=sink.serve_forever,daemon=True).start()
        override=Path(local)/'local.yaml'
        override.write_text(json.dumps({
            'receivers':{'otlp/lane':{'protocols':{'http':{'endpoint':f'127.0.0.1:{port}'}}}},
            'exporters':{'otlphttp/monium':{'traces_endpoint':f'http://127.0.0.1:{sink.server_port}/sink',
                          'tls':{'insecure':True},'encoding':'json','compression':'none'}}}))
        # The real cloud configuration is only validated, never started.
        with open(Path(local)/'collector.log','w+') as logs:
            proc=subprocess.Popen([binary,*configs,'--config='+str(override)],env=env,stdout=logs,stderr=logs)
            try:
                deadline=monotonic()+10
                while True:
                    conn=HTTPConnection('127.0.0.1',port,timeout=1)
                    try:
                        conn.request('POST','/v1/traces',json.dumps(fixture(lane)).encode(),
                                     {'Content-Type':'application/json'})
                        with conn.getresponse() as response:
                            assert response.status==200, f'fixture rejected: HTTP {response.status}'
                        break
                    except (ConnectionRefusedError,ConnectionResetError):
                        if proc.poll() is not None or monotonic()>deadline:
                            raise
                        sleep(.1)
                    finally:
                        conn.close()
                path,headers,body=exports.get(timeout=8)
                assert path=='/sink'
                assert headers['authorization']=='Application-Key '+DUMMY['MONIUM_TRACES_APPLICATION_KEY']
                assert headers['x-monium-project']==DUMMY['MONIUM_TRACES_PROJECT']
                assert SECRET not in json.dumps(body),'payload escaped allowlist'
                spans=[span for r in body['resourceSpans'] for s in r['scopeSpans'] for span in s['spans']]
                expected={1:'openai.chat',8:'memory_query.result_persist',
                          **{serial:'memory_query.'+stage for serial,stage in enumerate(QUERY_STAGES,9)}}
                seen={int(span['spanId'],16):span for span in spans}
                assert len(spans)==len(expected) and {i:p['name'] for i,p in seen.items()}==expected,'wrong span crossed filter'
                for i,span in seen.items():
                    assert not span.get('events') and not span.get('links')
                    counts=[a['value'] for a in span['attributes'] if a['key']=='memory.retry_count']
                    assert counts==([{'intValue':'2'}] if i==8 else []),'wrong retry metadata crossed filter'
                print(lane+': exact lane, sanitized span, no cloud connection')
            except BaseException:
                logs.seek(0)
                print(logs.read(),file=sys.stderr)
                raise
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                sink.shutdown()


if __name__=='__main__':
    if len(sys.argv)!=2:
        raise SystemExit('Usage: python3 smoke.py /path/to/otelcol-contrib')
    binary=str(Path(sys.argv[1]).resolve(strict=True))
    for lane in ('test','prod'):
        smoke(binary,lane)
