"""Mac transport for the protected Base machine API."""
import ctypes,json,re,uuid
from urllib.parse import urlsplit
from urllib.request import Request,build_opener
from .remote_client import NoRedirect,TransportError

class MachineClient:
    def __init__(self,config,*,opener=None,credential=None):
        self.config=config
        url=urlsplit(config['url'])
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.port or url.query or url.fragment or url.path!='/memory-api-prod':
            raise ValueError('Expected exact private PROD machine route')
        self.opener=opener or build_opener(NoRedirect())
        self.credential=credential or self._keychain
    def _keychain(self):
        service=self.config['keychain_service']; account=self.config['keychain_account']
        if not all(isinstance(s,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,120}',s) for s in (service,account)):
            raise ValueError('Invalid Keychain reference')
        lib=ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        find=lib.SecKeychainFindGenericPassword
        find.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_char_p,ctypes.c_uint32,ctypes.c_char_p,ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
        free=lib.SecKeychainItemFreeContent
        free.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
        service=service.encode('ascii');account=account.encode('ascii')
        length=ctypes.c_uint32(); data=ctypes.c_void_p()
        if find(None,len(service),service,len(account),account,ctypes.byref(length),ctypes.byref(data),None)!=0:
            raise ValueError('Keychain item unavailable')
        try:
            if length.value!=64 or not data.value: raise ValueError('Invalid Keychain item')
            return ctypes.string_at(data,length.value).decode('ascii')
        finally:
            if data.value: free(None,data)
    def _request(self,token,operation,arguments,expected=None):
        identity=str(uuid.uuid4())
        payload=dict(request_id=identity,operation=operation,arguments=arguments)
        if expected is not None:payload['expected']=expected
        req=Request(self.config['url'],data=json.dumps(payload,separators=(',',':')).encode(),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'},method='POST')
        with self.opener.open(req,timeout=20) as response:
            raw=response.read(512*1024+1)
            if len(raw)>512*1024:raise ValueError('response too large')
            received=json.loads(raw)
        if received['request_id']!=identity or received['environment']!='prod':
            raise ValueError('receipt crosses scope')
        proof={k:received[k] for k in ('release','generation','catalog_sha256')}
        if not isinstance(proof['release'],str) or not re.fullmatch(r'assertion-memory-v[0-9]+\.[0-9]+\.[0-9]+',proof['release']) or not all(isinstance(proof[k],str) and re.fullmatch(r'[a-f0-9]{64}',proof[k]) for k in ('generation','catalog_sha256')):
            raise ValueError('invalid release metadata')
        if expected is not None and proof!=expected:raise ValueError('release changed')
        return received,proof
    def call(self,name,arguments):
        if name not in {'memory_ingest_register','memory_ingest_report','memory_store_artifact','memory_capture_message'}:
            raise TransportError('Operation outside the Mac collector')
        try:
            token=self.credential()
            if not isinstance(token,str) or not re.fullmatch('[a-f0-9]{64}',token):
                raise ValueError('invalid credential')
            catalog,proof=self._request(token,'catalog',{})
            if catalog['capability']!='machine' or sum(t['name']==name for t in catalog['tools'])!=1:
                raise ValueError('required operation absent')
            receipt,_=self._request(token,name,arguments,proof)
            if receipt['status']!='ok' or not isinstance(receipt['result'],dict):
                raise ValueError('not a successful queue receipt')
            return receipt['result']
        except Exception:
            raise TransportError('Memory operation not confirmed') from None
