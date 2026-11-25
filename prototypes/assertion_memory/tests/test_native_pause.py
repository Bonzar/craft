"""Dedicated pause transport is not an MCP or read Graph credential."""
import asyncio
import unittest
from httpx import AsyncClient, ASGITransport

from prototypes.assertion_memory.native_pause import make_native_pause, via_test_effects, PATH, EFFECTS_CARRIER
from prototypes.assertion_memory.processing_control import Conflict, INITIAL


class NativePauseTest(unittest.TestCase):
    def test_only_exact_token_lane_actor_method_and_arguments(self):
        actor='a'*64
        token='c'*64
        calls=[]
        state={'stage':'test','paused':False,'revision':INITIAL}
        def read():
            calls.append('read')
            return state.copy()
        def change(*,stage,paused,expected_revision):
            calls.append('change')
            if expected_revision!=state['revision']:
                raise Conflict()
            state.update(paused=paused,revision='d'*32)
            return state.copy()
        route=make_native_pause(actor=actor,token=token,prepare=read,change=change)
        auth={'authorization':'Bearer '+token}
        prepare=dict(stage='test',action='prepare',actor=actor,arguments={})
        change_body={**prepare,'action':'set','arguments':dict(stage='test',paused=True,expected_revision=INITIAL)}
        async def run():
            async with AsyncClient(transport=ASGITransport(app=route),base_url='http://testserver') as c:
                for uri,headers,body,status in (
                  (PATH,{},prepare,403), (PATH,{'authorization':'Bearer '+'b'*64},prepare,403),
                  (PATH,auth,{**prepare,'actor':'e'*64},403), (PATH+'?x=y',auth,prepare,404),
                  ('/mcp',auth,prepare,404), (PATH,auth,{**prepare,'stage':'prod'},400),
                  (PATH,auth,{**prepare,'action':'reset'},400),
                  (PATH,auth,{**prepare,'arguments':{'scope':'anything'}},400)):
                    with self.subTest(uri=uri,status=status):
                        self.assertEqual((await c.post(uri,headers=headers,json=body)).status_code,status)
                self.assertEqual((await c.put(PATH,headers=auth,json=change_body)).status_code,404)
                self.assertEqual((await c.post(PATH,headers={**auth,'content-type':'application/json'},
                    content=b'{"stage":"prod","stage":"test"}')).status_code,400)
                self.assertEqual(calls,[])
                ready=await c.post(PATH,headers=auth,json=prepare)
                self.assertEqual(ready.json(),state)
                self.assertEqual(ready.headers['cache-control'],'private, no-store')
                self.assertEqual((await c.post(PATH,headers=auth,json=change_body)).json(),
                                 {'stage':'test','paused':True,'revision':'d'*32})
                self.assertEqual((await c.post(PATH,headers=auth,json=change_body)).status_code,409)
                self.assertEqual(calls,['read','change','change'])
        asyncio.run(run())

    def test_test_carrier_preserves_old_effects_and_never_falls_back_on_pause_denial(self):
        from starlette.responses import JSONResponse
        token, effect, actor = 'a'*64,'b'*64,'c'*64
        calls=[]
        async def existing(scope, receive, send):
            calls.append(('old',scope['path']))
            r=JSONResponse({'old':True},status_code=418)
            await r(scope,receive,send)
        def read():
            calls.append(('pause','read'))
            return dict(stage='test',paused=False,revision=INITIAL)
        def change(**args):
            calls.append(('pause','set'))
            return dict(stage='test',paused=args['paused'],revision='d'*32)
        native=make_native_pause(actor=actor,token=token,prepare=read,change=change)
        auth={'authorization':'Bearer '+token}
        generic={'authorization':'Bearer '+effect}
        prepare=dict(stage='test',action='prepare',actor=actor,arguments={})
        toggle={**prepare,'action':'set',
                'arguments':dict(stage='test',paused=True,expected_revision=INITIAL)}
        for stage, t, e in (('prod',token,effect),('test',None,effect),
                            ('test','malformed',effect),('test',token,None),
                            ('test',token,'malformed'),('test',token,token)):
            self.assertIs(via_test_effects(existing,native,stage=stage,pause_token=t,effect_token=e),existing)
        app=via_test_effects(existing,native,stage='test',pause_token=token,effect_token=effect)
        async def exercise():
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fake') as c:
                self.assertEqual((await c.post(EFFECTS_CARRIER,headers=generic,json=toggle)).status_code,418)
                self.assertEqual((await c.post(EFFECTS_CARRIER,headers=generic,json={'operation':'catalog'})).status_code,418)
                self.assertEqual((await c.post('/memory-mcp-prod-effects',headers=auth,json=toggle)).status_code,418)
                self.assertEqual((await c.post(EFFECTS_CARRIER,headers=auth,json={'operation':'catalog'})).status_code,400)
                self.assertEqual((await c.post(EFFECTS_CARRIER,headers=auth,json={**prepare,'actor':'e'*64})).status_code,403)
                self.assertEqual((await c.post(EFFECTS_CARRIER+'?x=1',headers=auth,json=prepare)).status_code,404)
                self.assertEqual((await c.put(EFFECTS_CARRIER,headers=auth,json=toggle)).status_code,404)
                duplicates=[('Authorization','Bearer '+token),('authorization','Bearer '+effect)]
                self.assertEqual((await c.post(EFFECTS_CARRIER,headers=duplicates,json=toggle)).status_code,403)
                self.assertEqual(calls,[('old',EFFECTS_CARRIER),('old',EFFECTS_CARRIER),
                                        ('old','/memory-mcp-prod-effects')])
                self.assertEqual((await c.post(EFFECTS_CARRIER,headers=auth,json=prepare)).status_code,200)
                self.assertEqual((await c.post(EFFECTS_CARRIER,headers=auth,json=toggle)).json()['paused'],True)
                self.assertEqual(calls[-2:],[('pause','read'),('pause','set')])
        asyncio.run(exercise())
