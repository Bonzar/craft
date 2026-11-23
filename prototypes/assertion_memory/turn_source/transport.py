"""Isolated standard stdio setup for the synthetic one-turn protocol proof."""
from collections import deque
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time

MAX_FRAME=1_048_576
MAX_RECEIVE_BYTES=8_388_608
MAX_RECEIVE_FRAMES=2048


def encode(message):
    return json.dumps(message,ensure_ascii=False,separators=(',',':')).encode()+b'\n'


def read_frame(stream):
    line=stream.readline(MAX_FRAME+1)
    if not line or len(line)>MAX_FRAME or not line.endswith(b'\n'):
        raise EOFError('MCP frame missing or oversized')
    obj=json.loads(line)
    if not isinstance(obj,dict):raise ValueError('MCP frame must be an object')
    return obj


class StdioTransport:
    def __init__(self, command, *, timeout=30, cancelled=lambda:False, allow_spawn=False, offline_test_process=None):
        # Construction does not invoke a provider. Explicitly enabling the test
        # constructor is required even if the supplied subprocess is synthetic.
        if not allow_spawn:
            raise RuntimeError('Subprocess proof is disabled by default.')
        self.command=tuple(command)
        self.seconds=timeout
        self.cancelled=cancelled
        self.offline_test_process=offline_test_process
        self.messages=deque()
        self.incoming=bytearray()
        self.request_id=0
        self.counter=0
        self.received_bytes=0
        self.closed=False
        if not self.command or not Path(self.command[0]).is_absolute():
            raise ValueError('A trusted absolute executable is required')
        if type(timeout) not in (int,float) or not 0 < timeout <= 300:
            raise ValueError('Invalid turn deadline')
        self.tools=['older_dialogue']

    def configure_mcp(self, dispatch):
        self.tmp=tempfile.TemporaryDirectory(prefix='am-turn-',dir='/tmp')
        root=Path(self.tmp.name)
        self.cwd=str(root/'work')
        Path(self.cwd).mkdir(mode=0o700)
        home=root/'home';home.mkdir(mode=0o700)
        runtime=root/'run';runtime.mkdir(mode=0o700)
        bridge=root/'bridge';bridge.mkdir(mode=0o700)
        self.deadline=time.monotonic()+self.seconds
        for name in ('request','response'):
            os.mkfifo(bridge/name,0o600)
        self.mcp_in=os.open(bridge/'request',os.O_RDWR|os.O_NONBLOCK)
        self.mcp_out=os.open(bridge/'response',os.O_RDWR|os.O_NONBLOCK)
        self.server_thread=threading.Thread(target=self._serve,args=(dispatch,),daemon=True)
        self.server_thread.start()
        py='/usr/bin/python3' if self.offline_test_process is not None else str(Path(sys.executable).resolve())
        relay=str(Path(__file__).with_name('mcp_relay.py').resolve())
        config=('analytics.enabled = false\n[features]\nshell_tool=false\nhooks=false\nplugins=false\napps=false\n'
            '[mcp_servers.source_job]\nrequired = true\ncommand = '+json.dumps(py)+'\nargs = '+json.dumps([relay,str(bridge)])+
            '\nenabled_tools = '+json.dumps(self.tools)+'\n')
        (home/'config.toml').write_text(config)
        env={'CODEX_HOME':str(home),'HOME':str(home),'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8',
             'XDG_RUNTIME_DIR':str(runtime),'XDG_CONFIG_HOME':str(home),'TMPDIR':str(root)}
        if self.offline_test_process is None:
            self.process=subprocess.Popen(self.command,cwd=self.cwd,env=env,start_new_session=True,
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
        else:
            self.process=self.offline_test_process.spawn(self.command,cwd=self.cwd,env=env,
                                                         broker_root=root,relay=relay)
        os.set_blocking(self.process.stdout.fileno(),False)
        os.set_blocking(self.process.stdin.fileno(),False)

    def _serve(self, dispatch):
        pending=bytearray()
        try:
            with selectors.DefaultSelector() as sel:
                sel.register(self.mcp_in,selectors.EVENT_READ)
                while not self.closed and not self.cancelled() and time.monotonic()<self.deadline:
                    if not sel.select(.05):continue
                    pending.extend(os.read(self.mcp_in,65536))
                    while b'\n' in pending:
                        line,_,pending=pending.partition(b'\n')
                        if len(line)>=MAX_FRAME:return
                        message=json.loads(line)
                        response=dispatch(message) if isinstance(message,dict) else None
                        if response is None:continue
                        result=memoryview(encode(response))
                        if len(result)>MAX_FRAME:return
                        while result and not self.closed:
                            self._wait(self.mcp_out,selectors.EVENT_WRITE)
                            try:result=result[os.write(self.mcp_out,result):]
                            except BlockingIOError:continue
                    if len(pending)>=MAX_FRAME:return
        except (ValueError,OSError,RuntimeError):
            pass
        finally:
            os.close(self.mcp_in)
            os.close(self.mcp_out)

    def _wait(self, file, event):
        with selectors.DefaultSelector() as sel:
            sel.register(file,event)
            while True:
                if self.closed or self.cancelled():raise RuntimeError('Generation cancelled')
                remaining=self.deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('Turn deadline expired')
                if sel.select(min(.08,remaining)):return

    def _send(self,message):
        data=memoryview(encode(message))
        if len(data)>MAX_FRAME:raise ValueError('App-server request exceeds frame limit')
        while data:
            self._wait(self.process.stdin,selectors.EVENT_WRITE)
            try:written=os.write(self.process.stdin.fileno(),data)
            except BlockingIOError:continue
            data=data[written:]

    def _receive(self):
        self.counter+=1
        if self.counter>MAX_RECEIVE_FRAMES:raise RuntimeError('App-server frame resource budget exhausted')
        while True:
            newline=self.incoming.find(b'\n')
            if newline>=0:
                if newline>=MAX_FRAME:raise ValueError('App-server frame exceeds limit')
                body=bytes(self.incoming[:newline]);del self.incoming[:newline+1]
                obj=json.loads(body)
                if not isinstance(obj,dict):raise ValueError('App-server frame must be an object')
                # App-server stdio JSON-RPC optionally omits jsonrpc.
                if obj.get('jsonrpc','2.0')!='2.0':raise ValueError('App-server frame version')
                return obj
            if len(self.incoming)>=MAX_FRAME:raise ValueError('App-server frame exceeds limit')
            self._wait(self.process.stdout,selectors.EVENT_READ)
            try:received=os.read(self.process.stdout.fileno(), min(65536,MAX_FRAME-len(self.incoming)))
            except BlockingIOError:continue
            if not received:raise EOFError('App-server exited before replying')
            self.received_bytes+=len(received)
            if self.received_bytes>MAX_RECEIVE_BYTES:
                raise RuntimeError('App-server byte resource budget exhausted')
            self.incoming.extend(received)

    def notify(self,method,params):
        self._send({'jsonrpc':'2.0','method':method,'params':params})

    def _notification(self,message):
        method=message.get('method')
        if not isinstance(method,str):raise ValueError('Invalid app-server notification')
        if 'id' in message:
            # No interactive or execution authorizations in this proof.
            self._send({'jsonrpc':'2.0','id':message['id'],
                        'error':{'code':-32601,'message':'Server requests are not authorized'}})
            return None
        return method,message.get('params',{})

    def call(self,method,params):
        self.request_id+=1
        rid=self.request_id
        self._send({'jsonrpc':'2.0','id':rid,'method':method,'params':params})
        while True:
            message=self._receive()
            if 'method' in message:
                notification=self._notification(message)
                if notification is not None:self.messages.append(notification)
                continue
            if type(message.get('id')) is not int or message['id']!=rid or 'result' not in message:
                raise RuntimeError('App-server rejected request or replied out of order')
            return message['result']

    def events(self):
        while self.messages:yield self.messages.popleft()
        while True:
            notification=self._notification(self._receive())
            if notification is not None:yield notification

    def close(self):
        if self.closed:return
        self.closed=True
        if hasattr(self,'process'):
            try:os.killpg(self.process.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:self.process.wait(.4)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid,signal.SIGKILL)
                self.process.wait(1)
            self.process.stdin.close()
            self.process.stdout.close()
        if getattr(self,'server_thread',None) is not None and self.server_thread.ident is not None:
            self.server_thread.join(1)
        else:
            for fd in ('mcp_in','mcp_out'):
                if hasattr(self,fd):os.close(getattr(self,fd))
        if hasattr(self,'tmp'):self.tmp.cleanup()
