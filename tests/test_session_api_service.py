import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.mcp_server import _register_source_tools

class SessionServiceTest(unittest.TestCase):
    def test_service_and_registered_tools_never_generate_or_write(self):
        with tempfile.TemporaryDirectory() as tmp, MemoryStore(str(Path(tmp)/'memory.sqlite3')) as store:
            service=MemoryService(store)
            event=service.capture_message('session','message','calculator','tool','synthetic output',0,'tool://fixture')['event']
            class FakeMCP:
                def __init__(self):self.tools={}
                def tool(self):
                    def register(fn):self.tools[fn.__name__]=fn;return fn
                    return register
            mcp=FakeMCP();_register_source_tools(mcp,service)
            for reserved in ('assistant','tool'):
                with self.assertRaises(ValueError):
                    service.capture_message('session','bad-'+reserved,reserved,'user','synthetic',2,'chat://fixture')
            before=store._connection.total_changes
            store._connection.execute('PRAGMA query_only=ON')
            sessions=asyncio.run(mcp.tools['memory_sessions']())
            messages=asyncio.run(mcp.tools['memory_session_messages']('session',message_id='message'))
            provenance=asyncio.run(mcp.tools['memory_provenance']([event['id']]))
            self.assertEqual(sessions['total'],1)
            self.assertEqual(messages['messages'][0]['role'],'tool')
            self.assertEqual(provenance['records'][0]['session_ids'],['session'])
            self.assertEqual(before,store._connection.total_changes)
            self.assertNotIn('synthetic output',json.dumps([sessions,messages,provenance]))

if __name__=='__main__':unittest.main()
