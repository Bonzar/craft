import asyncio
import unittest
from .browser_surface import browser_surface

class Surface(unittest.TestCase):
    def test_browser_only_has_no_path_rewrite_or_private_fallback(self):
        seen=[]
        async def graph(scope, receive, send):
            seen.append(scope)
        api=browser_surface(graph)
        async def test():
            messages=[]
            async def send(message):
                messages.append(message)
            for path in ('/mcp','/memory-api-prod','/memory-mcp-prod-effects','/api/v1','/','/visual'):
                await api({'type':'http','path':path,'raw_path':path.encode()},None,send)
                self.assertEqual(messages[-2]['status'],404)
            spoof={'type':'http','path':'/api/v1/session','raw_path':b'/%61pi/v1/session'}
            await api(spoof,None,send)
            self.assertEqual(messages[-2]['status'],404)
            self.assertEqual(seen,[])
            normal={'type':'http','method':'POST','path':'/api/v1/session','raw_path':b'/api/v1/session'}
            await api(normal,None,send)
            self.assertEqual(seen,[normal])
        asyncio.run(test())
