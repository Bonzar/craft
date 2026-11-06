"""Portable review delivery: no live network, processes, installation or messages."""
import copy
import importlib.util
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from urllib.parse import parse_qs, urlsplit

from . import MODULES_DIR
from jarvis import Storage, manifest, registry

TARGET = '01a1a668-37b8-78e3-871b-963c39e05df6'
ORIGIN = 'https://example.chatgpt.site'


def load(slug):
    part = registry.library_of(manifest.load(MODULES_DIR / slug))
    return part.load()


def event(cursor='1', ident='evt_message-1'):
    return {'eventId': ident, 'cursor': cursor, 'name': 'comment.created',
            'timestamp': '2026-11-05T10:00:00Z', 'data': {
                'session': 'review-session', 'review': 'revision-3', 'thread': 'discussion-1',
                'message_id': 'message-1', 'text': 'Исправь $(touch /tmp/no) и `опасное`',
                'quote': 'Предложенная правка', 'quote_truncated': False,
                'url': ORIGIN + '/?session=review-session'}}


class FakeClient:
    def __init__(self):
        self.cursor = '0'
        self.events = [event()]
        self.busy = False
        self.fail_ack = False
        self.acks = []

    def claim(self, session, target, instance):
        return {'cursor': self.cursor, 'events': copy.deepcopy(self.events),
                'busy': self.busy, 'leaseSeconds': 30}

    def ack(self, session, target, instance, cursor, event_id):
        self.acks.append(event_id)
        if self.fail_ack:
            raise RuntimeError('ack unavailable')
        self.cursor = cursor
        self.events = [e for e in self.events if e['eventId'] != event_id]
        return {'cursor': cursor}


class FakeAdapter:
    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, target, payload):
        if self.fail:
            raise RuntimeError('queue unavailable')
        self.sent.append((target, payload))
        return 'queue-1'


class ReviewDiscussionTest(unittest.TestCase):
    def setUp(self):
        self.lib = load('review-discussion')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(Path(self.tmp.name), TARGET)
        self.env = {'REVIEW_POLL_ENABLED': '1', 'REVIEW_URL': ORIGIN,
                    'REVIEW_SESSION': 'review-session', 'REVIEW_TOKEN': 'secret-not-for-journal'}
        self.config = self.lib.Config.from_env(self.env, TARGET, 'codex')
        self.client, self.adapter = FakeClient(), FakeAdapter()

    def consumer(self):
        return self.lib.Consumer(self.storage, self.config, self.adapter, self.client,
                                 instance='test-instance')

    def test_success_queues_exact_thread_with_event_data_and_ack(self):
        self.assertEqual(self.consumer().step(), 'delivered')
        self.assertEqual(len(self.adapter.sent), 1)
        target, payload = self.adapter.sent[0]
        self.assertEqual(target, TARGET)
        for value in ('evt_message-1', 'revision-3', 'Предложенная правка',
                      '$(touch /tmp/no)', ORIGIN):
            self.assertIn(value, payload)
        self.assertEqual(self.client.acks, ['evt_message-1'])
        self.assertNotIn('secret-not-for-journal', ''.join(
            p.read_text() for p in Path(self.tmp.name).rglob('*') if p.is_file()))

    def test_comment_delivery_is_readable_without_visible_json_envelope(self):
        item = event()
        item['data'].update(text='Test', quote='Отдельный технический документ.')
        expected = ('> Test\n\nК тексту:\n> Отдельный технический документ.\n\n'
                    '[Обсуждение](' + ORIGIN
                    + '/?session=review-session&review=revision-3&thread=discussion-1#event=evt_message-1)')
        self.assertEqual(self.lib.message(item), expected)
        self.assertTrue(self.lib.message(item).startswith('> Test\n'))
        self.assertLess(len(self.lib.message(item)), 400)

    def test_comment_delivery_quotes_every_line_and_preserves_the_full_comment(self):
        item = event()
        item['data']['text'] = 'Первая строка\n\n# Вторая строка\n' + 'текст ' * 1000
        item['data']['quote'] = 'Цитата\nсо второй строкой'
        payload = self.lib.message(item)
        self.assertIn('> Первая строка\n> \n> # Вторая строка\n> ' + 'текст ' * 1000,
                      payload)
        self.assertIn('> Цитата\n> со второй строкой', payload)

    def test_comment_delivery_bounds_quote_preview_and_keeps_revision_and_event_link(self):
        item = event()
        item['data']['quote'] = 'Длинная цитата ' * 1000
        before = copy.deepcopy(item)
        payload = self.lib.message(item)
        self.assertIn('К тексту (фрагмент):', payload)
        self.assertIn('…', payload)
        self.assertIn('&review=revision-3&thread=discussion-1#event=evt_message-1)', payload)
        self.assertNotIn(item['data']['quote'], payload)
        self.assertLess(len(payload), 700)
        self.assertEqual(item, before)

    def test_comment_delivery_labels_a_server_truncated_quote_as_a_fragment(self):
        item = event()
        item['data']['quote_truncated'] = True
        self.assertIn('К тексту (фрагмент):', self.lib.message(item))

    def test_region_comments_and_replies_keep_the_exact_image_thread_in_the_link(self):
        for thread, event_id in (('image-one', 'evt_initial-message'),
                                 ('image-one', 'evt_unrelated-reply-id'),
                                 ('image-two', 'evt_another-reply-id')):
            with self.subTest(thread=thread, event_id=event_id):
                item = event(ident=event_id)
                item['data'].update(thread=thread, quote='Выделенная область', text='Test')
                link = self.lib.message(item).rsplit('[Обсуждение](', 1)[1][:-1]
                parsed = urlsplit(link)
                self.assertEqual(parse_qs(parsed.query)['session'], ['review-session'])
                self.assertEqual(parse_qs(parsed.query).get('thread'), [thread])
                self.assertEqual(parsed.fragment, 'event=' + event_id)

    def test_failed_ack_restarts_without_requeue(self):
        self.client.fail_ack = True
        with self.assertRaises(RuntimeError):
            self.consumer().step()
        self.client.fail_ack = False
        self.assertEqual(self.consumer().step(), 'delivered')
        self.assertEqual(len(self.adapter.sent), 1)
        self.assertEqual(self.client.acks, ['evt_message-1', 'evt_message-1'])

    def test_busy_lease_never_queues_or_acks(self):
        self.client.busy = True
        self.assertEqual(self.consumer().step(), 'busy')
        self.assertEqual(self.adapter.sent, [])
        self.assertEqual(self.client.acks, [])

    def test_queue_failure_does_not_ack_or_advance(self):
        self.adapter.fail = True
        with self.assertRaises(RuntimeError):
            self.consumer().step()
        self.assertEqual(self.client.acks, [])
        self.adapter.fail = False
        self.consumer().step()
        self.assertEqual(len(self.adapter.sent), 1)

    def test_replayed_event_id_is_not_sent_again(self):
        self.consumer().step()
        self.client.cursor = '0'
        self.client.events = [event()]
        self.consumer().step()
        self.assertEqual(len(self.adapter.sent), 1)

    def test_bad_session_event_and_marks_are_rejected_before_queue(self):
        for broken in ({**event(), 'name': 'mark.updated'},
                       {**event(), 'data': {**event()['data'], 'session': 'another'}}):
            with self.subTest(broken=broken['name']):
                self.client.events = [broken]
                with self.assertRaises(ValueError):
                    self.consumer().step()
        self.assertEqual(self.adapter.sent, [])

    def test_urls_and_target_are_validated_without_sending_secret(self):
        for bad in ('http://example.chatgpt.site', 'https://evil.example',
                    'https://example.chatgpt.site/?secret=x',
                    'https://u:p@example.chatgpt.site', 'https://example.chatgpt.site:444'):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                self.lib.Config.from_env({**self.env, 'REVIEW_URL': bad}, TARGET, 'codex')
        with self.assertRaises(ValueError):
            self.lib.Config.from_env({**self.env, 'REVIEW_TARGET_THREAD': 'name'}, TARGET, 'codex')
        with self.assertRaises(ValueError):
            self.lib.Config.from_env({**self.env, 'REVIEW_TARGET_THREAD':
                '11111111-1111-4111-8111-111111111111'}, TARGET, 'codex')

    def test_http_uses_json_browser_user_agent_and_refuses_redirect(self):
        class Response(io.BytesIO):
            headers = {'Content-Type': 'application/json'}
        response = Response(b'{"cursor":"0","events":[],"busy":false,"leaseSeconds":30}')
        with patch.object(self.lib.urllib.request, 'build_opener') as make:
            make.return_value.open.return_value = response
            client = self.lib.Client(ORIGIN, 'test-secret')
            client.claim('review-session', TARGET, 'instance')
        request = make.return_value.open.call_args.args[0]
        self.assertTrue(request.get_header('User-agent').startswith('Mozilla/5.0'))
        self.assertEqual(request.get_header('Oai-sites-authorization'), 'Bearer test-secret')
        self.assertEqual(request.get_header('X-review-client'), 'codex')
        self.assertLessEqual(make.return_value.open.call_args.kwargs['timeout'], 10)
        self.assertIsNone(self.lib.NoRedirect().redirect_request(
            request, None, 302, '', {}, 'https://other.chatgpt.site'))

    def test_http_accepts_full_valid_unicode_event_batch(self):
        class Response(io.BytesIO):
            headers = {'Content-Type': 'application/json'}
        items = [event(str(i + 1), 'evt_' + str(i)) for i in range(50)]
        for item in items:
            item['data']['text'] = '漢' * 10000
            item['data']['quote'] = '漢' * 8000
        payload = {'cursor': '0', 'events': items, 'busy': False, 'leaseSeconds': 30}
        response = Response(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        with patch.object(self.lib.urllib.request, 'build_opener') as make:
            make.return_value.open.return_value = response
            result = self.lib.Client(ORIGIN, 'secret').claim('review-session', TARGET, 'instance')
        self.assertEqual(len(result['events']), 50)

    def test_pid_lock_blocks_duplicate_and_accepts_stale_pid(self):
        first = self.lib.ProcessLock(self.storage)
        second = self.lib.ProcessLock(self.storage)
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
        finally:
            first.close()
        self.storage.write_json('review-discussion-process.json', {'pid': 99999999})
        self.assertTrue(second.acquire())
        second.close()

    def test_auto_start_prepares_launcher_but_only_starts_once(self):
        module_dir = Path(self.tmp.name) / 'modules/review-discussion'
        module_dir.mkdir(parents=True)
        shutil.copytree(MODULES_DIR / 'review-discussion/data', module_dir / 'data')
        with patch.object(self.lib.subprocess, 'Popen') as spawn:
            spawn.return_value.pid = 12345
            result = self.lib.start(self.config, self.storage, module_dir, self.env)
        self.assertEqual(result, 'started')
        self.assertTrue((module_dir / 'bin/review-discussion').is_file())
        self.assertTrue(spawn.call_args.kwargs['start_new_session'])
        self.assertNotIn('secret-not-for-journal', repr(spawn.call_args.args))
        lock = self.lib.ProcessLock(self.storage)
        self.assertTrue(lock.acquire())
        try:
            with patch.object(self.lib.subprocess, 'Popen') as duplicate:
                self.assertEqual(self.lib.start(self.config, self.storage, module_dir, self.env),
                                 'already_running')
                duplicate.assert_not_called()
        finally:
            lock.close()


class CodexDeliveryTest(unittest.TestCase):
    def setUp(self):
        self.lib = load('review-discussion-codex')

    def test_queue_uses_argv_no_resume_no_shell_and_explicit_home(self):
        env = {'REVIEW_CODEX_EXECUTABLE': '/portable/codex', 'CODEX_HOME': '/explicit/profile',
               'REVIEW_TOKEN': 'must-not-reach-codex', 'PATH': '/bin'}
        with patch.object(self.lib.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 0,
                'Queued message queue-123 for thread ' + TARGET + '.\n', '')
            receipt = self.lib.Adapter(env).send(TARGET, 'Текст $(do-not-run)')
        self.assertEqual(receipt, 'queue-123')
        argv = run.call_args.args[0]
        self.assertEqual(argv, ['/portable/codex', 'queue', '--thread', TARGET,
                               '--message', 'Текст $(do-not-run)'])
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs['env']['CODEX_HOME'], '/explicit/profile')
        self.assertNotIn('REVIEW_TOKEN', run.call_args.kwargs['env'])

    def test_queue_error_is_redacted_and_never_treated_as_delivery(self):
        with patch.object(self.lib.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 1, '', 'secret-token and payload')
            with self.assertRaisesRegex(RuntimeError, '^codex_queue_failed$'):
                self.lib.Adapter({'REVIEW_CODEX_EXECUTABLE': '/codex'}).send(TARGET, 'text')

    def test_success_for_wrong_thread_is_rejected(self):
        with patch.object(self.lib.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 0,
                'Queued message x for thread 11111111-1111-4111-8111-111111111111.', '')
            with self.assertRaises(RuntimeError):
                self.lib.Adapter({'REVIEW_CODEX_EXECUTABLE': '/codex'}).send(TARGET, 'text')


class BootstrapSafetyTest(unittest.TestCase):
    def setUp(self):
        self.lib = load('review-discussion')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = Storage(Path(self.tmp.name) / 'state', TARGET)
        self.env = {'REVIEW_POLL_ENABLED': '1', 'REVIEW_URL': ORIGIN,
                    'REVIEW_SESSION': 'review-session', 'REVIEW_TOKEN': 'redact-this-secret',
                    'REVIEW_CODEX_EXECUTABLE': '/portable/codex'}
        self.config = self.lib.Config.from_env(self.env, TARGET, 'codex')
        self.module_dir = MODULES_DIR / 'review-discussion'

    def test_existing_consumer_with_different_binding_is_not_silently_reused(self):
        lock = self.lib.ProcessLock(self.storage)
        self.assertTrue(lock.acquire())
        self.storage.write_json('review-discussion-process.json', {
            'pid': os.getpid(), 'target': TARGET, 'session': 'some-other-review', 'url': ORIGIN})
        try:
            with self.assertRaisesRegex(RuntimeError, 'review_consumer_binding_mismatch'):
                self.lib.start(self.config, self.storage, self.module_dir, self.env)
        finally:
            lock.close()

    def test_manual_bootstrap_failure_writes_core_trace_without_credentials(self):
        argv = ['start', '--session', TARGET, '--harness', 'codex',
                '--state-dir', str(self.storage.persistent_dir)]
        with patch.dict(os.environ, {**self.env, 'REVIEW_TOKEN': ''}, clear=True), \
                patch('sys.stderr', new_callable=io.StringIO), \
                patch.object(self.lib.subprocess, 'Popen') as spawn:
            self.assertEqual(self.lib.main(self.module_dir, argv), 1)
        spawn.assert_not_called()
        lines = self.storage.read_lines('trace.jsonl')
        self.assertTrue(lines)
        line = json.loads(lines[-1])
        self.assertEqual(line['module'], 'review-discussion')
        self.assertFalse(line['delivered'])
        self.assertNotIn('redact-this-secret', ''.join(lines))

    def test_stop_sets_runtime_mode_and_status_exposes_dead_consumer(self):
        self.storage.write_json('review-discussion-process.json', {'pid': 99999999})
        argv = ['status', '--session', TARGET, '--harness', 'codex',
                '--state-dir', str(self.storage.persistent_dir)]
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(self.lib.main(self.module_dir, argv), 0)
        self.assertFalse(json.loads(output.getvalue())['running'])
        with patch('sys.stdout', new_callable=io.StringIO):
            self.lib.main(self.module_dir, ['stop', *argv[1:]])
        self.assertFalse(self.lib.enabled(self.storage, self.module_dir))
