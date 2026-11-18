"""R24-R30 public SDK regression: capturing alone must create shared overviews.

Only six real public tools are available. This harness intentionally allows
capture/status/history; no query/process or private builder can manufacture the
initial result. The deterministic provider proves orchestration, not LLM quality.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[3]
PROVIDER = Path(__file__).with_name('automatic_overview_provider.py')
STATEMENTS = [
    ('planning', 'Ava', 'Release requires a signed permit. Only during the pilot.'),
    ('operations', 'Bo', 'Release requires backup approval when the lead is absent.'),
    ('review', 'Cy', 'Release excludes the archive until its audit is complete.'),
]


class AutomaticOverviewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='automatic-overview-sdk-')
        self.addCleanup(self.folder.cleanup)
        self.db = Path(self.folder.name) / 'memory.sqlite3'
        self.log = Path(self.folder.name) / 'provider.jsonl'
        self.gate = Path(self.folder.name) / 'provider-gate'
        self.calls = []
        self.receipts = []
        self.public_reads = []
        self.history_presentation_pins = {}
        self.client_context = None
        self.session_context = None

    async def asyncTearDown(self):
        # The server launches independent workers; allow their configured idle
        # timeout to finish before removing their temporary SQLite directory.
        await asyncio.sleep(1.2)
        requests = self.requests()
        owned_pids = {r['processing']['worker_pid'] for r in self.receipts
                      if r.get('processing', {}).get('worker_pid')}
        owned_pids.update(r[key] for r in requests for key in ('pid', 'worker_pid'))
        deadline = time.monotonic() + 5
        alive = set(owned_pids)
        while alive and time.monotonic() < deadline:
            for pid in list(alive):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    alive.remove(pid)
            if alive:
                await asyncio.sleep(.1)
        evidence_dir = os.environ.get('AUTOMATIC_OVERVIEW_EVIDENCE_DIR')
        if evidence_dir:
            output = Path(evidence_dir)
            output.mkdir(parents=True, exist_ok=True)
            proof = {'test': self.id(), 'public_tool_calls': self.calls, 'capture_receipts': self.receipts,
                     'public_reads': self.public_reads, 'provider_requests': requests,
                     'owned_pids': sorted(owned_pids), 'remaining_owned_pids': sorted(alive)}
            (output / (self._testMethodName + '.json')).write_text(json.dumps(proof, indent=2))
        self.assertFalse(alive, 'owned SDK worker/provider descendants must exit: ' + repr(alive))

    async def start(self, provider=True, *, gate_task=None):
        env = dict(os.environ, PYTHONPATH=str(ROOT), AUTOMATIC_OVERVIEW_PROVIDER_LOG=str(self.log))
        if gate_task:
            env.update(AUTOMATIC_OVERVIEW_GATE_PATH=str(self.gate), AUTOMATIC_OVERVIEW_GATE_TASK=gate_task)
        args = ['-m', 'prototypes.assertion_memory.mcp_server', '--db', str(self.db),
                '--provider', 'subprocess' if provider else 'none', '--worker-idle-seconds', '1']
        if provider:
            args += ['--model-command-json', json.dumps([sys.executable, str(PROVIDER)])]
        self.client_context = stdio_client(StdioServerParameters(command=sys.executable, args=args, env=env, cwd=str(ROOT)))
        read, write = await self.client_context.__aenter__()
        self.session_context = ClientSession(read, write)
        self.client = await self.session_context.__aenter__()
        await self.client.initialize()
        tools = await self.client.list_tools()
        self.assertEqual({tool.name for tool in tools.tools}, {'memory_capture_message', 'memory_process',
            'memory_query', 'memory_source', 'memory_status', 'memory_history'})

    async def stop(self):
        if self.session_context:
            await self.session_context.__aexit__(None, None, None)
            self.session_context = None
        if self.client_context:
            await self.client_context.__aexit__(None, None, None)
            self.client_context = None

    async def call(self, name, **arguments):
        self.assertIn(name, {'memory_capture_message', 'memory_status', 'memory_history'})
        self.calls.append(name)
        result = await self.client.call_tool(name, arguments)
        self.assertFalse(result.isError, result)
        value = result.structuredContent
        if value is None:
            value = json.loads(''.join(item.text for item in result.content if item.type == 'text'))
        if name != 'memory_capture_message':
            self.public_reads.append({'tool': name, 'arguments': arguments, 'response': value})
        return value

    async def capture(self, session, actor, text, *, number=0, scope='campus', role='user'):
        receipt = await self.call('memory_capture_message', session_id=session, message_id=f'message-{number}',
            actor_id=actor, role=role, text=text, position=number, source=f'fixture://{session}/{number}', scope=scope)
        self.receipts.append(receipt)
        return receipt

    async def wait_sources(self, count, scope='campus'):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = await self.call('memory_status', scope=scope)
            self.assertEqual(state['error_count'], 0, state)
            if state['assertion_count'] == count and state['pending_count'] == 0:
                return state
            await asyncio.sleep(.1)
        self.fail('Source pipeline did not complete: ' + json.dumps(state))

    def derived_rows(self, state):
        directory = state.get('derived_overviews')
        if directory is None:
            return []
        self.assertIsInstance(directory, dict, 'public directory contract is an object')
        self.assertIsInstance(directory['entries'], list)
        self.assertGreaterEqual(directory['total'], len(directory['entries']))
        return directory['entries']

    async def wait_overview(self, scope='campus', previous=None):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = await self.call('memory_status', scope=scope)
            for row in self.derived_rows(state):
                if row.get('current_version_id') and row['current_version_id'] != previous and row.get('state') == 'idle':
                    return row
            await asyncio.sleep(.1)
        tasks = [r['task'] for r in self.requests()]
        self.fail('NO_INITIAL_OVERVIEW: public capture completed but no accepted automatic root is '
                  'discoverable using read-only status/history. state=' + json.dumps(state) + ' provider_tasks=' + repr(tasks))

    def requests(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    async def history_section(self, view_id, version_id, section):
        key=(view_id,version_id,section)
        pins=self.history_presentation_pins.get(key,{}) if section=='metadata' else {}
        response = await self.call('memory_history', view_id=view_id, version_id=version_id, section=section,**pins)
        if section=='metadata':
            self.history_presentation_pins.setdefault(key,{name:response[name]
                for name in ('presentation_cutoff','name_watermark')})
        parts = [response['content']]
        while response.get('continuation'):
            continuation = response['continuation']
            response = await self.call('memory_history', **continuation.get('arguments', continuation.get('args', {})))
            parts.append(response['content'])
        value = ''.join(parts)
        return value if section == 'summary' else json.loads(value)

    async def domain_summaries(self, view_id, version_id):
        metadata = await self.history_section(view_id, version_id, 'metadata')
        if metadata.get('overview_review', {}).get('assembly_coverage') == 'navigation_index':
            self.assertEqual(metadata['overview_review']['verification'], 'mechanical_membership')
            return '\n'.join([await self.domain_summaries(key, value)
                              for key, value in metadata['upstream'].items()])
        return await self.history_section(view_id, version_id, 'summary')

    async def wait_gate(self, task):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            rows = [r for r in self.requests() if r['task'] == task]
            if rows and Path(str(self.gate) + '.claimed').exists():
                return rows[-1]
            await asyncio.sleep(.05)
        self.fail('NO_INITIAL_OVERVIEW: background never reached the synthetic provider barrier for ' + task)

    def release_gate(self):
        Path(str(self.gate) + '.release').touch()

    async def test_R24_capture_alone_produces_first_shared_overview(self):
        await self.start()
        try:
            for session, actor, text in STATEMENTS:
                receipt = await self.capture(session, actor, text)
                self.assertTrue(receipt['processing']['scheduled'], receipt)
                self.assertIsInstance(receipt['processing']['worker_pid'], int)
            state = await self.wait_sources(3)
            self.assertEqual(state['event_count'], 3)
            self.assertEqual(state['assertion_count'], 3, 'the canonical source must never be copied per theme')
            self.assertTrue(any(r['task'].startswith('Extract') for r in self.requests()))
            root = await self.wait_overview()
            summary = await self.history_section(root['root_view_id'], root['current_version_id'], 'summary')
            evidence = await self.history_section(root['root_view_id'], root['current_version_id'], 'evidence')
            self.assertEqual(len({row['id'] for row in evidence}), 3)
            self.assertEqual({row['actor'] for row in evidence}, {'Ava', 'Bo', 'Cy'})
            domain = await self.domain_summaries(root['root_view_id'], root['current_version_id'])
            for _, _, text in STATEMENTS:
                self.assertIn(text, domain, 'all conditions must remain in the accepted thematic children')
            for row in evidence:
                self.assertRegex(domain, r'\[(?:source:)?' + re.escape(row['id']) + r'\]')
            metadata = await self.history_section(root['root_view_id'], root['current_version_id'], 'metadata')
            self.assertEqual(metadata['overview_review']['status'], 'accepted')
            self.assertEqual(metadata['overview_review']['assembly_coverage'], 'navigation_index')
            self.assertTrue(metadata['upstream'], 'the directory must link substantive reviewed themes')
            self.assertIsInstance(root['current_check_id'], int)
            self.assertIsNotNone(root['completed_frontier'])
            self.assertEqual(root['desired_frontier'], root['completed_frontier'])
            checks = await self.history_section(root['root_view_id'], root['current_version_id'], 'checks')
            self.assertTrue(any(row['id'] == root['current_check_id'] and row['version_id'] == root['current_version_id']
                                for row in checks), 'descriptor must resolve to an actual stored immutable check')
            self.assertEqual(set(self.calls), {'memory_capture_message', 'memory_status', 'memory_history'})
        finally:
            await self.stop()

    async def test_R25_clarification_revises_affected_summary_and_keeps_unrelated_material(self):
        await self.start()
        try:
            for session, actor, text in STATEMENTS:
                await self.capture(session, actor, text)
            await self.capture('supplies', 'Dee', 'Stationery orders require a monthly budget.')
            await self.wait_sources(4)
            first = await self.wait_overview()
            old_summary = await self.history_section(first['root_view_id'], first['current_version_id'], 'summary')
            old_metadata = await self.history_section(first['root_view_id'], first['current_version_id'], 'metadata')
            children = {}
            for view_id, version_id in old_metadata['upstream'].items():
                children[view_id] = (version_id, await self.history_section(view_id, version_id, 'summary'))
            stationery = [(view_id, version_id) for view_id, (version_id, text) in children.items() if 'Stationery' in text]
            self.assertEqual(len(stationery), 1)
            before = len(self.requests())
            clarification = 'Release clarification: the pilot permit remains required even when backup approval is present.'
            await self.capture('clarification', 'Eli', clarification)
            await self.wait_sources(5)
            current = await self.wait_overview(previous=first['current_version_id'])
            self.assertTrue(first['root_request_id'])
            self.assertEqual(current['root_request_id'], first['root_request_id'])
            routes = await self.history_section(current['root_view_id'], None, 'routes')
            self.assertEqual({row['request_id'] for row in routes}, {current['root_request_id']})
            self.assertTrue({first['root_view_id'], current['root_view_id']} <= {row['view_id'] for row in routes})
            summary = await self.history_section(current['root_view_id'], current['current_version_id'], 'summary')
            metadata = await self.history_section(current['root_view_id'], current['current_version_id'], 'metadata')
            self.assertIn(clarification, await self.domain_summaries(current['root_view_id'], current['current_version_id']))
            self.assertEqual(await self.history_section(first['root_view_id'], first['current_version_id'], 'summary'), old_summary)
            self.assertEqual(await self.history_section(first['root_view_id'], first['current_version_id'], 'metadata'), old_metadata)
            for view_id, version_id in stationery:
                self.assertEqual(metadata['upstream'][view_id], version_id)
            self.assertFalse(any(r['task'] == 'What does office supplies require?' for r in self.requests()[before:]),
                             'unchanged theme must incur no writer call')
        finally:
            await self.stop()

    async def test_R28_shared_membership_and_cross_session_topics_without_source_copies(self):
        await self.start()
        try:
            inputs = [
                ('one', 'Ava', 'Release requires a signed permit.', 0),
                ('two', 'Bo', 'Release excludes archives pending audit.', 0),
                ('one', 'Cy', 'Stationery orders require budget approval.', 1),
                ('three', 'Dee', 'Release and Stationery both require the same approval register.', 0),
            ]
            for session, actor, text, number in inputs:
                await self.capture(session, actor, text, number=number)
            await self.wait_sources(4)
            root = await self.wait_overview()
            metadata = await self.history_section(root['root_view_id'], root['current_version_id'], 'metadata')
            self.assertEqual(len(metadata['upstream']), 2, 'sessions and actors must not become theme owners')
            memberships = []
            for view_id, version_id in metadata['upstream'].items():
                memberships.append(await self.history_section(view_id, version_id, 'evidence'))
            shared = {r['id'] for r in memberships[0]} & {r['id'] for r in memberships[1]}
            self.assertEqual(len(shared), 1, 'the shared canonical source belongs to both themes')
            self.assertEqual(len({r['id'] for group in memberships for r in group}), 4)
            state = await self.call('memory_status', scope='campus')
            self.assertEqual(state['assertion_count'], 4)
            self.assertEqual(len(self.derived_rows(state)), 1, 'one shared root per authorized scope')
        finally:
            await self.stop()

    async def test_R29_unconfigured_model_is_honest_and_restart_resumes_automatically(self):
        await self.start(provider=False)
        try:
            for session, actor, text in STATEMENTS:
                await self.capture(session, actor, text)
            # Metadata reads must not fabricate either canonical interpretations
            # or accepted semantic summaries when extraction has no provider.
            state = await self.call('memory_status', scope='campus')
            self.assertEqual(state['event_count'], 3)
            self.assertEqual(state['assertion_count'], 0)
            self.assertFalse(state['model_configured'])
            self.assertTrue(state['pending_count'])
            rows = self.derived_rows(state)
            self.assertTrue(rows, 'pending scope derivation must be publicly discoverable without a model')
            self.assertTrue(all(row['state'] == 'waiting_model' and not row.get('current_version_id') for row in rows))
            self.assertEqual(self.requests(), [])
        finally:
            await self.stop()
        await self.start()
        try:
            await self.wait_sources(3)
            root = await self.wait_overview()
            summary = await self.history_section(root['root_view_id'], root['current_version_id'], 'summary')
            domain = await self.domain_summaries(root['root_view_id'], root['current_version_id'])
            for _, _, text in STATEMENTS:
                self.assertIn(text, domain)
        finally:
            await self.stop()


    async def test_R29_assistant_context_alone_never_becomes_canonical_overview_truth(self):
        await self.start()
        try:
            await self.capture('assistant-only', 'assistant-proposer',
                'Release proposal: perhaps omit every audit.', role='assistant')
            await asyncio.sleep(1)
            state = await self.call('memory_status', scope='campus')
            self.assertEqual(state['event_count'], 1)
            self.assertEqual(state['assertion_count'], 0)
            self.assertEqual(state['pending_count'], 0)
            self.assertFalse(any(row.get('current_version_id') for row in self.derived_rows(state)))
            self.assertFalse(any(row['task'].startswith('Extract') for row in self.requests()))
        finally:
            await self.stop()

    async def test_R27_killed_owned_worker_resumes_accepted_leaf_without_duplicate_provider_call(self):
        # Buffer raw captures first so the configured worker freezes all three.
        await self.start(provider=False)
        try:
            for session, actor, text in STATEMENTS:
                await self.capture(session, actor, text)
            await self.capture('supplies', 'Dee', 'Stationery orders require a monthly budget.')
        finally:
            await self.stop()
        task = 'What does office supplies require?'
        await self.start(gate_task=task)
        try:
            paused = await self.wait_gate(task)
            # Read-only inspection times the crash after a real accepted leaf;
            # it never constructs views or changes recovery/lease state.
            with sqlite3.connect('file:' + str(self.db) + '?mode=ro', uri=True) as connection:
                saved = connection.execute('SELECT id,snapshot FROM memory_view_versions').fetchall()
            accepted = [(key, json.loads(snapshot)) for key, snapshot in saved
                        if json.loads(snapshot).get('overview_review', {}).get('status') == 'accepted']
            self.assertTrue(accepted, 'crash must occur after a real accepted leaf publication')
            requests_before = self.requests()
            # These exact PIDs are emitted by the fixture descended from our
            # SDK-started service; no name search or broad process mask exists.
            os.kill(paused['worker_pid'], signal.SIGKILL)
            os.kill(paused['pid'], signal.SIGKILL)
            await self.stop()
            self.release_gate()
            await self.start()
            await self.wait_sources(4)
            root = await self.wait_overview()
            summary = await self.history_section(root['root_view_id'], root['current_version_id'], 'summary')
            domain = await self.domain_summaries(root['root_view_id'], root['current_version_id'])
            for _, _, text in STATEMENTS:
                self.assertIn(text, domain)
            for key, snapshot in accepted:
                self.assertEqual(await self.history_section(snapshot['view_id'], key, 'summary'), snapshot['summary'])
            completed_keys = [(r['task'], r['instructions'], json.dumps(r['records'], sort_keys=True)) for r in requests_before if r['pid'] != paused['pid']]
            after = self.requests()[len(requests_before):]
            self.assertFalse(set(completed_keys) & {(r['task'], r['instructions'], json.dumps(r['records'], sort_keys=True)) for r in after},
                             'durably completed provider requests must not repeat after crash')
        finally:
            self.release_gate()
            await self.stop()

    async def test_R30_new_capture_during_frozen_generation_cannot_leak_into_old_history(self):
        await self.start(provider=False)
        try:
            for session, actor, text in STATEMENTS:
                await self.capture(session, actor, text)
        finally:
            await self.stop()
        await self.start(gate_task='Discover reusable memory themes')
        try:
            await self.wait_gate('Discover reusable memory themes')
            late = 'Release late clarification requires a second audit after the pilot.'
            await self.capture('late', 'Eli', late)
            await self.capture('private', 'Fox', 'Release private scope secret must never enter campus.', scope='private')
            self.release_gate()
            await self.wait_sources(4)
            root = await self.wait_overview()
            # Materialized dependency definitions are immutable. Follow all
            # roots in the stable request family through the public route
            # history, then read their pinned versions through normal history.
            routes = await self.history_section(root['root_view_id'], None, 'routes')
            self.assertTrue(root['root_request_id'])
            self.assertEqual({row['request_id'] for row in routes}, {root['root_request_id']})
            by_id = {}
            for view_id in sorted({row['view_id'] for row in routes}):
                for version in await self.history_section(view_id, None, 'versions'):
                    by_id[version['id']] = version
            versions = list(by_id.values())
            self.assertGreaterEqual(len(versions), 2, 'a later desired frontier must not erase the in-progress frozen generation')
            old = next((v for v in versions if len(v['evidence']) == 3), None)
            self.assertIsNotNone(old, 'the first frozen generation must publish its original three-source lineage')
            current = next(v for v in versions if v['id'] == root['current_version_id'])
            self.assertNotIn(late, old['summary'])
            self.assertIn(late, await self.domain_summaries(current['view_id'], current['id']))
            self.assertNotIn('private scope secret', json.dumps(versions))
            self.assertEqual(len(current['evidence']), 4)
            self.assertEqual(await self.history_section(old['view_id'], old['id'], 'summary'), old['summary'])
            # Capture API has no timestamp override. These actual immutable
            # recorded timestamps establish the old/new ordering directly.
            self.assertLess(old['created_at'], current['created_at'])
            self.assertEqual({r['actor'] for r in old['evidence']}, {'Ava', 'Bo', 'Cy'})
        finally:
            self.release_gate()
            await self.stop()



if __name__ == '__main__':
    unittest.main()
