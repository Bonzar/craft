"""Additive exact-path adapter for composition into the existing owner app."""
import asyncio
from hashlib import sha256
import hmac
import sqlite3
from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.routing import Route

from .auth import COOKIE, check_host, check_origin
from .common import APIError
from prototypes.assertion_memory.machine_device_registry import MachineDeviceError, MachineDeviceRegistry

PATH='/api/v1/processing/devices'


class DeviceOwnerHTTP:
    """Keep disabled without a trusted registry and existing release guard."""
    def __init__(self, *, stage, registry, release_guard):
        if (type(registry) is not MachineDeviceRegistry or registry.lane!=stage
            or not callable(release_guard)):
            raise ValueError('invalid stage device owner binding')
        self.registry=registry
        self.release_guard=release_guard

    def routes(self):
        return [Route(PATH,self.handle,methods=['GET','PUT'],name='owner-machine-devices'),
                Route(PATH+'/prepare',self.handle,methods=['POST'],name='owner-machine-devices-prepare')]

    def _guarded(self, fun, *args):
        with self.release_guard():
            return fun(*args)

    async def ready(self):
        try:
            await asyncio.to_thread(self._guarded,self.registry.status)
            return True
        except (OSError,sqlite3.Error,ValueError):
            return False

    async def handle(self, request):
        from .app import body
        from .browser_owner_api import require_transport
        require_transport(request)
        check_host(request)
        public_origin=request.app.state.public_origin
        if (not public_origin or request.headers.get('host')!=urlsplit(public_origin).netloc
            or request.url.path not in (PATH,PATH+'/prepare')
            or request.scope.get('raw_path')!=request.url.path.encode('ascii')):
            raise APIError('not_found','Unknown device resource',404)
        if request.scope.get('query_string'):
            raise APIError('invalid_request','Unexpected query',400)
        csrf=request.app.state.owner.session(request)
        credential=request.cookies.get(COOKIE,'')
        if not csrf or not credential:
            raise APIError('owner_required','Owner session required',401)
        try:
            if request.method in ('GET','HEAD') and request.url.path==PATH:
                status=await asyncio.to_thread(self._guarded,self.registry.status)
                return JSONResponse(status,headers={'Cache-Control':'no-store'})
            check_origin(request,required=True)
            if request.headers.getlist('origin')!=[request.app.state.public_origin]:
                raise APIError('origin_denied','Same-origin request required',403)
            if request.headers.getlist('content-type')!=['application/json']:
                raise APIError('invalid_request','Expected JSON',415)
            token=request.headers.getlist('x-csrf-token')
            if len(token)!=1 or len(token[0])>96:
                raise APIError('operation_denied','Operation proof required',403)
            data=await body(request,limit=1024,unique=True)
            binding=sha256(credential.encode()).hexdigest()
            if request.method=='POST' and request.url.path==PATH+'/prepare':
                if not hmac.compare_digest(token[0].encode(),csrf.encode()):
                    raise APIError('operation_denied','Owner CSRF required',403)
                status=await asyncio.to_thread(self._guarded,self.registry.prepare,binding,data)
            elif request.method=='PUT' and request.url.path==PATH:
                status=await asyncio.to_thread(self._guarded,self.registry.commit,binding,data,token[0])
            else:
                raise APIError('not_found','Unknown device resource',404)
            return JSONResponse(status,headers={'Cache-Control':'no-store'})
        except MachineDeviceError as err:
            key=str(err)
            if key in ('stale_device_registry','configured_device_immutable',
                       'device_enrollment_not_available','device_not_managed'):
                raise APIError('device_registry_changed','Refresh devices before retrying',409) from None
            if key in ('invalid_device_owner','invalid_device_operation_proof'):
                raise APIError('operation_denied','Owner operation proof not accepted',403) from None
            if key=='invalid_device_intent':
                raise APIError('invalid_request','Invalid device action',400) from None
            if key=='device_operation_limit':
                raise APIError('device_busy','Try again later',429) from None
            raise APIError('device_unavailable','Device registry unavailable',503) from None
        except (OSError,sqlite3.Error):
            raise APIError('device_unavailable','Device registry unavailable',503) from None
