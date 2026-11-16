"""Synthetic MCP wire/header-helper verification; no live endpoint/credentials."""
import json
from pathlib import Path
import tempfile
import unittest
from .test_assertion_memory_capture import load_capture


class Response:
    def __init__(self, value, content_type='application/json'):
        self.body = json.dumps(value).encode()
        self.headers = {'Content-Type': content_type}
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, limit): return self.body[:limit]


class RemoteTest(unittest.TestCase):
    def setUp(self):
        load_capture()
        from synthetic_memory_adapter.remote_client import Client, TransportError
        self.Client, self.TransportError = Client, TransportError
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token = Path(self.tmp.name) / 'token'
        self.token.write_text('synthetic-token')
        self.token.chmod(0o600)
        self.config = {'url': 'https://fiction.test/mcp', 'bearer_token_file': str(self.token)}

    def test_content_only_and_structured_receipts_with_initialize_handshake(self):
        for structured in (True, False):
            requests = []
            class Opener:
                def open(_, request, timeout):
                    message = json.loads(request.data)
                    requests.append(message)
                    self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-token')
                    if message['method'] == 'initialize': result = {'protocolVersion': '2025-06-18'}
                    elif message['method'] == 'notifications/initialized': return Response({})
                    else:
                        value = {'event': {'id': 'receipt'}}
                        result = {'structuredContent': value} if structured else {'content': [{'type':'text','text':json.dumps(value)}]}
                    return Response({'jsonrpc':'2.0','id': message['id'], 'result':result})
            client = self.Client(self.config, opener=Opener())
            self.assertEqual(client.capture({'role':'user'}), {'event': {'id':'receipt'}})
            self.assertEqual([r['method'] for r in requests], ['initialize','notifications/initialized','tools/call'])
            self.assertEqual(requests[-1]['params']['name'], 'memory_capture_message')

    def test_transport_error_never_echoes_credentials_or_remote_body(self):
        class Opener:
            def open(_, request, timeout): raise OSError('synthetic-token remote personal text')
        with self.assertRaises(self.TransportError) as error:
            self.Client(self.config, opener=Opener()).capture({})
        self.assertNotIn('synthetic-token', str(error.exception))
        self.assertNotIn('personal text', str(error.exception))

    def test_nonprivate_token_file_is_rejected(self):
        self.token.chmod(0o644)
        class Opener:
            def open(_, *args, **kwargs): raise AssertionError('must fail before network')
        with self.assertRaises(self.TransportError): self.Client(self.config, opener=Opener()).capture({})
