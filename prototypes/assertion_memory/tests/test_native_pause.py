"""Dedicated pause transport is not an MCP or read Graph credential."""
import asyncio
import json
import unittest
from httpx import AsyncClient, ASGITransport

from prototypes.assertion_memory.native_pause import make_native_pause, PATH
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
