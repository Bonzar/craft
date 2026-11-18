"""Authenticated remote clients share the core without exposing SQLite."""
import tempfile
import unittest
import json
import asyncio
import os
from functools import wraps
from unittest.mock import patch
from pathlib import Path

import httpx

from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.http_server import create_http_app, load_token


def running_server(method):
    @wraps(method)
    async def run(self):
        async with self.app.lifespan():
            await method(self)
    return run


def tool_data(response):
    body = response.json()['result']
    return body.get('structuredContent') or json.loads(body['content'][0]['text'])


class MemoryHTTPTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = ServiceConfig(str(Path(self.directory.name) / 'memory.sqlite3'), 'none')
        self.token = 'synthetic-test-key-' + 'x' * 40
        self.app = create_http_app(self.config, self.token, 'memory.test:9443', background=False)

    def client(self, token=None, origin=None, host='memory.test:9443'):
        headers = {'Accept': 'application/json, text/event-stream'}
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        if origin:
            headers['Origin'] = origin
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                base_url='https://' + host, headers=headers)

    async def rpc(self, client, method, params=None):
        return await client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1,
                                              'method': method, 'params': params or {}})

    @running_server
    async def test_unauthenticated_and_wrong_token_cannot_list_or_capture(self):
        for token in (None, 'wrong'):
            async with self.client(token) as client:
                reply = await self.rpc(client, 'tools/list')
                self.assertEqual(reply.status_code, 401)
                self.assertNotIn('memory_capture_message', reply.text)

    @running_server
    async def test_installer_and_enrollment_are_not_served(self):
        for token,expected in ((None,401),(self.token,404)):
            async with self.client(token) as client:
                for path in ('/memory/client','/memory/client.zip','/memory/enrollment/requests'):
                    reply=await client.get(path)
                    self.assertEqual(reply.status_code,expected)

    @running_server
    async def test_authenticated_independent_clients_share_idempotent_capture(self):
        args = dict(session_id='synthetic-session', message_id='message-1', actor_id='user',
                    role='user', text='Synthetic campus delivery remains conditional.',
                    position=0, source='fixture://session/message-1')
        receipts = []
        for _ in range(2):
            async with self.client(self.token) as client:
                response = await self.rpc(client, 'tools/call',
                    {'name': 'memory_capture_message', 'arguments': args})
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()['result']
                self.assertFalse(body.get('isError', False), body)
                receipts.append(tool_data(response)['event']['id'])
        self.assertEqual(receipts[0], receipts[1])
        async with self.client(self.token) as client:
            response = await self.rpc(client, 'tools/call', {'name': 'memory_sessions'})
            data = tool_data(response)
            self.assertEqual(data['sessions'][0]['message_count'], 1)

    @running_server
    async def test_bearer_does_not_disable_host_or_origin_protection(self):
        for values in ({'host': 'attacker.test'}, {'origin': 'https://attacker.test'}):
            async with self.client(self.token, **values) as client:
                reply = await self.rpc(client, 'tools/list')
                self.assertIn(reply.status_code, (400, 403, 421))

    @running_server
    async def test_conflicting_retry_preserves_first_message(self):
        args = dict(session_id='s', message_id='m', actor_id='user', role='user',
                    text='First message', position=0, source='fixture://s/m')
        async with self.client(self.token) as client:
            first = await self.rpc(client, 'tools/call', {'name': 'memory_capture_message', 'arguments': args})
            other = await self.rpc(client, 'tools/call', {'name': 'memory_capture_message',
                                                        'arguments': {**args, 'text': 'Overwritten'}})
            self.assertFalse(first.json()['result'].get('isError', False))
            self.assertTrue(other.json()['result']['isError'])

    async def test_capture_schedules_processing_after_startup_worker_can_exit(self):
        with patch('prototypes.assertion_memory.mcp_server.WorkerLauncher.start',
                   return_value={'scheduled': False}) as start:
            app = create_http_app(self.config, self.token, 'memory.test:9443', background=True)
            async with app.lifespan():
                self.assertEqual(start.call_count, 1)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='https://memory.test:9443', headers={
                        'Authorization': 'Bearer ' + self.token,
                        'Accept': 'application/json, text/event-stream'}) as client:
                    await self.rpc(client, 'tools/call', {'name': 'memory_capture_message',
                        'arguments': dict(session_id='later', message_id='later', actor_id='user',
                            role='user', text='A later arrival', position=0, source='fixture://later')})
                self.assertEqual(start.call_count, 2, 'capture must wake an idle worker independently of startup')

    async def test_real_asgi_startup_and_shutdown_are_forwarded(self):
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        task = asyncio.create_task(self.app({'type': 'lifespan'}, incoming.get, outgoing.put))
        await incoming.put({'type': 'lifespan.startup'})
        self.assertEqual(await asyncio.wait_for(outgoing.get(), 3), {'type': 'lifespan.startup.complete'})
        async with self.client(self.token) as client:
            reply = await self.rpc(client, 'tools/list')
            self.assertIn('memory_query', reply.text)
        await incoming.put({'type': 'lifespan.shutdown'})
        self.assertEqual(await asyncio.wait_for(outgoing.get(), 3), {'type': 'lifespan.shutdown.complete'})
        await task

    async def test_public_mount_host_is_explicit_and_keeps_authentication(self):
        app = create_http_app(self.config, self.token, 'memory.test:9443', background=False,
                              additional_hosts=['memory.public', 'memory.public:443'])
        async with app.lifespan():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='https://memory.public', headers={
                        'Accept': 'application/json, text/event-stream'}) as client:
                denied = await self.rpc(client, 'tools/list')
                self.assertEqual(denied.status_code, 401)
                client.headers['Authorization'] = 'Bearer ' + self.token
                allowed = await self.rpc(client, 'tools/list')
                self.assertEqual(allowed.status_code, 200)
                self.assertIn('memory_query', allowed.text)
                client.headers['Host'] = 'attacker.public'
                rejected = await self.rpc(client, 'tools/list')
                self.assertIn(rejected.status_code, (400, 403, 421))


class TokenFileTest(unittest.TestCase):
    def test_host_uuid_comes_from_named_environment_without_reading_a_file(self):
        value='00000000-0000-4000-8000-000000000001'
        with patch.dict(os.environ,{'SYNTHETIC_MEMORY_TOKEN':value}), patch('os.open',side_effect=AssertionError('must not open key file')):
            self.assertEqual(load_token(env_var='SYNTHETIC_MEMORY_TOKEN'),value)
        with patch.dict(os.environ,{'SYNTHETIC_MEMORY_TOKEN':'not-a-uuid'}):
            with self.assertRaises(ValueError):load_token(env_var='SYNTHETIC_MEMORY_TOKEN')
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(ValueError):load_token(env_var='SYNTHETIC_MEMORY_TOKEN')

    def test_ambiguous_file_and_environment_auth_is_rejected(self):
        with self.assertRaises(ValueError):load_token(Path('/unused'),env_var='SYNTHETIC_MEMORY_TOKEN')

    def test_caller_store_cannot_mix_two_databases(self):
        from prototypes.assertion_memory.mcp_server import create_server
        from prototypes.assertion_memory.storage import MemoryStore
        with tempfile.TemporaryDirectory() as directory:
            with MemoryStore(str(Path(directory) / 'other.sqlite3')) as store:
                config = ServiceConfig(str(Path(directory) / 'intended.sqlite3'), 'none')
                with self.assertRaises(ValueError):
                    create_server(config, store=store, background=False)

    def test_token_file_must_be_private_and_nonempty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'token'
            path.write_text('x' * 48)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                load_token(path)
            path.chmod(0o600)
            self.assertEqual(load_token(path), 'x' * 48)
            path.write_text('short')
            with self.assertRaises(ValueError):
                load_token(path)
