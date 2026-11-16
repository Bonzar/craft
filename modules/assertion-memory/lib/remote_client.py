"""Small synchronous MCP Streamable HTTP sender, used only by the outbox worker."""
import json
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .headers import token

MAX_RESPONSE = 256 * 1024


class TransportError(RuntimeError): pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class Client:
    def __init__(self, config, *, opener=None, timeout=15):
        self.config = config
        self.opener = opener or build_opener(NoRedirect())
        self.timeout = timeout
        self.session_id = None
        self.protocol = '2025-06-18'
        self.sequence = 0
        self.initialized = False

    def _request(self, method, params, *, notification=False):
        self.sequence += 1
        message = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if not notification: message['id'] = self.sequence
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
                   'MCP-Protocol-Version': self.protocol}
        value = token(self.config)
        if value: headers['Authorization'] = 'Bearer ' + value
        if self.session_id: headers['Mcp-Session-Id'] = self.session_id
        request = Request(self.config['url'], data=json.dumps(message).encode(), headers=headers, method='POST')
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE + 1)
                session = response.headers.get('Mcp-Session-Id')
                if session: self.session_id = session
                if notification: return None
                if len(body) > MAX_RESPONSE: raise TransportError('Oversized MCP response')
                if 'text/event-stream' in response.headers.get('Content-Type', ''):
                    values = [json.loads(part[5:].strip()) for part in body.decode().splitlines() if part.startswith('data:')]
                    result = next((v for v in values if v.get('id') == self.sequence), None)
                else: result = json.loads(body)
                if not isinstance(result, dict) or result.get('id') != self.sequence or 'error' in result:
                    raise TransportError('Invalid/error MCP response')
                return result['result']
        except Exception:
            # urllib and server error text can include credentials. Never propagate it.
            raise TransportError('Memory endpoint request failed') from None

    def call(self, name, arguments):
        try:
            if not self.initialized:
                result = self._request('initialize', {'protocolVersion': self.protocol, 'capabilities': {},
                    'clientInfo': {'name': 'jarvis-memory-outbox', 'version': '1'}})
                self.protocol = result.get('protocolVersion', self.protocol)
                self._request('notifications/initialized', {}, notification=True)
                self.initialized = True
            result = self._request('tools/call', {'name': name, 'arguments': arguments})
            if result.get('isError'): raise TransportError('Memory tool rejected capture')
            structured = result.get('structuredContent')
            if isinstance(structured, dict): return structured
            text = '\n'.join(part.get('text', '') for part in result.get('content', []) if part.get('type') == 'text')
            value = json.loads(text)
            if not isinstance(value, dict): raise TransportError('Memory tool receipt is not an object')
            return value
        except Exception:
            self.initialized = False
            self.session_id = None
            raise TransportError('Memory tool delivery failed') from None

    def capture(self, arguments):
        return self.call('memory_capture_message', arguments)
