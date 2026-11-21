"""Reference contract: public MCP calls, real SQLite/worker, scripted model only."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ModuleNotFoundError as exc:
    if exc.name != 'mcp':
        raise
    ClientSession = None


ROOT = Path(__file__).resolve().parents[3]
SCOPE = 'synthetic-greenhouse'
SESSION = 'synthetic-session'
SPAN = 'Заслонка теплицы открывается только после проверки датчика.'
RAW = 'Журнал учебной теплицы. ' + SPAN
THOUGHT = 'Проверка датчика обязательна перед открытием заслонки теплицы.'

PROVIDER = """
import json
from pathlib import Path
import sys

request = json.loads(sys.stdin.read().split('\\n\\n', 1)[1])
task = request['task']
with Path(sys.argv[1]).open('a', encoding='utf-8') as log:
    log.write(task + '\\n')
if task == 'Construct thought':
    options = [fragment['option'] for row in request['records'] if row['role'] == 'source'
               for fragment in row.get('fragments', []) if fragment['text'] == sys.argv[2]]
    assert len(options) == 1
    result = {'thoughts': [{'text': sys.argv[3], 'question': None, 'support_fragments': options}],
              'needs_detail': None}
elif task == 'Verify thought':
    assert any(row['role'] == 'draft' and row.get('text') == sys.argv[3]
               for row in request['records'])
    assert any(fragment.get('selected_for_grounding') and fragment['text'] == sys.argv[2]
               for row in request['records'] if row['role'] == 'source'
               for fragment in row.get('fragments', []))
    result = {'verdict': 'accept', 'text': 'The draft retains the original prerequisite.'}
    if 'open_support' in request['response_schema'].get('properties', {}):
        result['open_support'] = None
else:
    raise AssertionError('unexpected provider task: ' + task)
print(json.dumps(result, ensure_ascii=False))
"""


@unittest.skipIf(ClientSession is None, 'install prototypes/assertion_memory/requirements-mcp.txt')
class CanonicalMcpReferenceFlow(unittest.IsolatedAsyncioTestCase):
    async def tool(self, name, **arguments):
        result = await self.client.call_tool(name, arguments)
        self.assertFalse(result.isError, result)
        return result.structuredContent if result.structuredContent is not None else json.loads(
            ''.join(item.text for item in result.content if item.type == 'text')
        )

    async def section(self, name, **arguments):
        arguments['limit'] = 113  # Exercise pinned continuation, not just inline responses.
        parts = []
        snapshot = None
        for _ in range(30):
            page = await self.tool(name, **arguments)
            self.assertEqual(page['offset'], sum(map(len, parts)))
            self.assertEqual(page['encoding'], 'json')
            snapshot = page['snapshot_id'] if snapshot is None else snapshot
            self.assertEqual(page['snapshot_id'], snapshot)
            parts.append(page['content'])
            if page['response_complete']:
                self.assertIsNone(page['continuation'])
                return json.loads(''.join(parts))
            arguments = page['continuation']['arguments']
        self.fail('section did not terminate within the synthetic page bound')

    async def test_capture_process_verified_publication_and_immutable_source(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script, log = base / 'provider.py', base / 'provider.log'
            script.write_text(PROVIDER, encoding='utf-8')
            args = [
                '-m', 'prototypes.assertion_memory.mcp_server',
                '--db', str(base / 'reference.sqlite3'), '--provider', 'subprocess',
                '--model-command-json', json.dumps([sys.executable, str(script), str(log), SPAN, THOUGHT]),
                '--processing-session-ids-json', '[]', '--source-workers', '1',
                '--worker-idle-seconds', '0.1',
            ]
            params = StdioServerParameters(command=sys.executable, args=args, cwd=str(ROOT))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    self.client = client
                    await client.initialize()
                    message = dict(session_id=SESSION, message_id='synthetic-message',
                                   actor_id='fixture-user', role='user', text=RAW, position=0,
                                   source='fixture://greenhouse/0', scope=SCOPE)
                    receipt = await self.tool('memory_capture_message', **message)
                    event = receipt['event']['id']
                    self.assertEqual(receipt['job']['status'], 'waiting_model')
                    self.assertFalse(receipt['processing']['scheduled'])
                    replay = await self.tool('memory_capture_message', **message)
                    self.assertEqual(replay['event']['id'], event)
                    before = await self.tool('memory_status', scope=SCOPE, section='navigation_catalog')
                    self.assertEqual(before['thoughts'], [])
                    self.assertFalse(log.exists())

                    launch = await self.tool('memory_process', limit=1, session_ids=[SESSION])
                    self.assertTrue(launch['scheduled'], launch)
                    async with asyncio.timeout(15):
                        while True:
                            catalog = await self.tool('memory_status', scope=SCOPE, section='navigation_catalog')
                            if catalog['thoughts']:
                                break
                            await asyncio.sleep(0.05)
                    self.assertEqual(catalog['total'], 1)
                    thought = catalog['thoughts'][0]
                    identity = thought['id']
                    self.assertEqual(thought['version'], 1)
                    self.assertEqual(thought['origin']['role'], 'user')

                    overview = await self.section('memory_source', record_id=identity, section='overview')
                    self.assertEqual(overview['text'], THOUGHT)
                    self.assertEqual(overview['metadata']['verification'], 'accepted')
                    references = await self.section('memory_source', record_id=identity, section='references')
                    self.assertEqual(len(references), 1)
                    reference = references[0]
                    self.assertEqual(reference['event_id'], event)
                    start, end = reference['span']['start'], reference['span']['end']
                    self.assertEqual((start, end), (RAW.index(SPAN), len(RAW)))
                    evidence = await self.tool('memory_source', record_id=event, start=start, end=end)
                    self.assertEqual(evidence['span']['text'], SPAN)
                    self.assertEqual(evidence['span']['event_id'], event)
                    self.assertTrue(evidence['response_complete'])
                    self.assertFalse(evidence['source_text_complete'])
                    history = await self.section('memory_history', view_id=identity, section='versions')
                    self.assertEqual([(row['id'], row['version'], row['text']) for row in history],
                                     [(identity, 1, THOUGHT)])
                    self.assertEqual(log.read_text(encoding='utf-8').splitlines(),
                                     ['Construct thought', 'Verify thought'])
