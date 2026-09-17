"""Цепочка стартового контекста: сборка у поставщиков, куски и их порядок.

Харнеса тут нет: копии запускаются теми же процессами и с тем же событием, что
запустил бы Claude, а порядок кусков берётся из порядка завершения процессов —
именно так его берёт и сам Claude (замер 16.09.2026).
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jarvis.storage import Storage

from . import CORE_SOURCE, INSTALLER, MODULES_DIR

BASE = 'start-context-claude'
CHUNK = 9_900


def session_event(session_id: str, source: str = 'startup') -> str:
    return json.dumps({
        'hook_event_name': 'SessionStart',
        'session_id': session_id,
        'cwd': '/work',
        'source': source,
    })


class Chain:
    """Общая установка: база копиями и два поставщика рядом."""

    copies = 3
    provider_chars = CHUNK * 2 + 100  # два полных куска и хвост: одной строкой не уехало бы

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.home = cls.root / 'home'
        cls.home.mkdir()
        cls.settings_root = cls.root / 'settings'
        source = cls.root / 'source'
        source.mkdir()

        shutil.copytree(MODULES_DIR / BASE, source / BASE)
        (source / BASE / 'module.toml').write_text(
            f'slug = "{BASE}"\nevents = ["session-start", "after-compact"]\n'
            f'harness = "claude"\ncopies = {cls.copies}\n',
            encoding='utf-8',
        )
        # Поставщик данных: путь без кода, текст известен посимвольно.
        data = source / 'big-data' / 'data'
        data.mkdir(parents=True)
        (source / 'big-data' / 'module.toml').write_text(
            'slug = "big-data"\nfor = ["start-context-*"]\n', encoding='utf-8'
        )
        cls.provider_text = ''.join(
            chr(0x430 + (number % 32)) for number in range(cls.provider_chars)
        )
        (data / 'hello.md').write_text(cls.provider_text, encoding='utf-8')
        # Поставщик кода: его функцию база обязана позвать.
        lib = source / 'code-provider' / 'lib'
        lib.mkdir(parents=True)
        (source / 'code-provider' / 'module.toml').write_text(
            'slug = "code-provider"\nfor = ["start-context-*"]\n', encoding='utf-8'
        )
        (lib / '__init__.py').write_text(
            'def provide(event, storage):\n'
            '    return f"код-поставщик на событии {event.event}"\n',
            encoding='utf-8',
        )

        subprocess.run(
            [sys.executable, str(INSTALLER),
             '--settings-dir', str(cls.settings_root),
             '--modules', str(source),
             '--core', str(CORE_SOURCE)],
            check=True, capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def entry(self, number: int) -> Path:
        slug = BASE if self.copies == 1 else f'{BASE}-{number}'
        return self.settings_root / 'jarvis' / 'modules' / slug / 'hooks' / 'module.py'

    def fire_all(self, session_id: str, source: str = 'startup') -> list[str]:
        """Запустить все копии разом и вернуть их вывод в порядке завершения."""
        payload = session_event(session_id, source)
        done: list[tuple[float, int, subprocess.CompletedProcess]] = []

        def one(number: int):
            result = subprocess.run(
                [sys.executable, str(self.entry(number)),
                 '--event', 'session-start', '--event', 'after-compact'],
                input=payload, text=True, capture_output=True,
                env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
            )
            done.append((time.time(), number, result))
            return result

        with ThreadPoolExecutor(max_workers=self.copies) as pool:
            list(pool.map(one, range(1, self.copies + 1)))
        for _, number, result in done:
            self.assertEqual(result.returncode, 0, f'копия {number}: {result.stderr}')
        return [result.stdout for _, _, result in sorted(done, key=lambda item: item[0])]

    @staticmethod
    def context_of(stdout: str) -> str | None:
        if not stdout.strip():
            return None
        payload = json.loads(stdout)
        return payload['hookSpecificOutput']['additionalContext']

    def expected(self, event: str) -> str:
        return (
            f'## big-data\n\n{self.provider_text}\n\n'
            f'## code-provider\n\nкод-поставщик на событии {event}'
        )

    def state(self, session_id: str, event: str = 'session-start') -> dict:
        storage = Storage(self.home / '.local' / 'state' / 'jarvis', session_id)
        return storage.read_json(f'start-context-{event}.json', default={})


class ChainTest(Chain, unittest.TestCase):
    """Цепочка целиком: текст уехал полностью и в порядке."""

    def test_the_chain_delivers_the_whole_text_in_order(self) -> None:
        printed = [self.context_of(out) for out in self.fire_all('chain-order')]
        chunks = [piece for piece in printed if piece is not None]
        self.assertEqual(len(chunks), self.copies)
        self.assertEqual(''.join(chunks), self.expected('session-start'))
        for piece in chunks[:-1]:
            self.assertEqual(len(piece), CHUNK)

    def test_providers_are_glued_by_slug_with_a_heading(self) -> None:
        self.fire_all('chain-heading')
        joined = ''.join(self.state('chain-heading')['chunks'])
        self.assertTrue(joined.startswith('## big-data\n\n'))
        self.assertIn('## code-provider\n\n', joined)

    def test_a_code_provider_is_called_with_the_event(self) -> None:
        self.fire_all('chain-event', source='compact')
        joined = ''.join(self.state('chain-event', 'after-compact')['chunks'])
        self.assertIn('код-поставщик на событии after-compact', joined)

    def test_the_state_of_the_run_lands_in_the_session_zone(self) -> None:
        self.fire_all('chain-state')
        state = self.state('chain-state')
        self.assertEqual(state['copies'], self.copies)
        self.assertFalse(state['truncated'])
        self.assertEqual(
            {entry['slug'] for entry in state['providers']}, {'big-data', 'code-provider'}
        )
        self.assertIsNone(state['providers'][0]['error'])

    def test_a_second_event_in_one_session_collects_anew(self) -> None:
        # Маркеры прошлого прогона лежат в той же зоне сессии: после сжатия
        # копии обязаны дождаться свежих, а не напечатать по старым.
        self.fire_all('chain-twice')
        first = self.state('chain-twice')['generation']
        printed = [self.context_of(out) for out in self.fire_all('chain-twice', source='compact')]
        chunks = [piece for piece in printed if piece is not None]
        self.assertNotEqual(self.state('chain-twice', 'after-compact')['generation'], first)
        self.assertEqual(''.join(chunks), self.expected('after-compact'))


class TruncationTest(Chain, unittest.TestCase):
    """Текста больше, чем несёт цепочка: хвост режется и это видно."""

    copies = 2
    provider_chars = CHUNK * 3

    def test_the_tail_is_cut_and_says_so(self) -> None:
        chunks = [piece for piece in (self.context_of(out) for out in self.fire_all('cut-order'))
                  if piece is not None]
        self.assertEqual(len(chunks), self.copies)
        self.assertTrue(''.join(chunks).startswith('## big-data\n\n'))
        self.assertTrue(chunks[-1].endswith('обрезан: он длиннее цепочки копий]'))

    def test_the_cut_is_written_down(self) -> None:
        self.fire_all('cut-state')
        state = self.state('cut-state')
        self.assertTrue(state['truncated'])
        self.assertEqual(len(state['chunks']), self.copies)
        self.assertGreater(state['total_chars'], sum(len(piece) for piece in state['chunks']))


class NoProvidersTest(unittest.TestCase):
    """Поставщиков нет — копии молчат, и никто никого не ждёт."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.home = cls.root / 'home'
        cls.home.mkdir()
        cls.settings_root = cls.root / 'settings'
        source = cls.root / 'source'
        source.mkdir()
        shutil.copytree(MODULES_DIR / BASE, source / BASE)
        (source / BASE / 'module.toml').write_text(
            f'slug = "{BASE}"\nevents = ["session-start"]\nharness = "claude"\ncopies = 3\n',
            encoding='utf-8',
        )
        subprocess.run(
            [sys.executable, str(INSTALLER),
             '--settings-dir', str(cls.settings_root),
             '--modules', str(source),
             '--core', str(CORE_SOURCE)],
            check=True, capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_every_copy_is_silent_and_none_waits_out_the_timeout(self) -> None:
        started = time.time()
        results = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            for number in range(1, 4):
                entry = self.settings_root / 'jarvis' / 'modules' / f'{BASE}-{number}' / 'hooks' / 'module.py'
                results.append(pool.submit(
                    subprocess.run,
                    [sys.executable, str(entry), '--event', 'session-start'],
                    input=session_event('empty'), text=True, capture_output=True,
                    env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
                ))
            done = [future.result() for future in results]
        for result in done:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
        self.assertLess(time.time() - started, 30)


if __name__ == '__main__':
    unittest.main()


class SlowGatherTest(unittest.TestCase):
    """Сбор идёт долго: копии ждут его, а не сдаются по таймеру."""

    copies = 3
    gather_seconds = 3

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.home = cls.root / 'home'
        cls.home.mkdir()
        cls.settings_root = cls.root / 'settings'
        source = cls.root / 'source'
        source.mkdir()
        shutil.copytree(MODULES_DIR / BASE, source / BASE)
        (source / BASE / 'module.toml').write_text(
            f'slug = "{BASE}"\nevents = ["session-start"]\nharness = "claude"\ncopies = {cls.copies}\n',
            encoding='utf-8',
        )
        lib = source / 'slow-provider' / 'lib'
        lib.mkdir(parents=True)
        (source / 'slow-provider' / 'module.toml').write_text(
            'slug = "slow-provider"\nfor = ["start-context-*"]\n', encoding='utf-8'
        )
        (lib / '__init__.py').write_text(
            'import time\n\n\n'
            'def provide(event, storage):\n'
            f'    time.sleep({cls.gather_seconds})\n'
            f'    return "медленно собранный текст " * {CHUNK // 10}\n',
            encoding='utf-8',
        )
        subprocess.run(
            [sys.executable, str(INSTALLER),
             '--settings-dir', str(cls.settings_root),
             '--modules', str(source),
             '--core', str(CORE_SOURCE)],
            check=True, capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def entry(self, number: int) -> Path:
        return self.settings_root / 'jarvis' / 'modules' / f'{BASE}-{number}' / 'hooks' / 'module.py'

    def test_every_copy_waits_out_a_slow_gather(self) -> None:
        payload = session_event('slow')
        with ThreadPoolExecutor(max_workers=self.copies) as pool:
            results = list(pool.map(
                lambda number: subprocess.run(
                    [sys.executable, str(self.entry(number)), '--event', 'session-start'],
                    input=payload, text=True, capture_output=True,
                    env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
                ),
                range(1, self.copies + 1),
            ))
        printed = []
        for number, result in enumerate(results, start=1):
            self.assertEqual(result.returncode, 0, f'копия {number}: {result.stderr}')
            if result.stdout.strip():
                printed.append(json.loads(result.stdout)['hookSpecificOutput']['additionalContext'])
        self.assertEqual(len(printed), self.copies, 'кто-то сдался, не дождавшись сбора')


class DeadLeaderTest(unittest.TestCase):
    """Копия 1 умерла, не оставив состояния: остальные молчат и не висят."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.home = cls.root / 'home'
        cls.home.mkdir()
        cls.settings_root = cls.root / 'settings'
        source = cls.root / 'source'
        source.mkdir()
        shutil.copytree(MODULES_DIR / BASE, source / BASE)
        (source / BASE / 'module.toml').write_text(
            f'slug = "{BASE}"\nevents = ["session-start"]\nharness = "claude"\ncopies = 2\n',
            encoding='utf-8',
        )
        subprocess.run(
            [sys.executable, str(INSTALLER),
             '--settings-dir', str(cls.settings_root),
             '--modules', str(source),
             '--core', str(CORE_SOURCE)],
            check=True, capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_a_follower_gives_up_when_the_leader_is_gone(self) -> None:
        session = 'dead-leader'
        storage = Storage(self.home / '.local' / 'state' / 'jarvis', session)
        # Маркер готовности от процесса, которого уже нет: ровно то состояние,
        # в котором копия 1 умерла, не успев записать состояние прогона.
        dead = subprocess.Popen([sys.executable, '-c', 'pass'])
        dead.wait()
        storage.write_json(
            'start-context-ready-session-start.json',
            {'generation': 'g', 'pid': dead.pid, 'at': time.time()},
        )
        entry = self.settings_root / 'jarvis' / 'modules' / f'{BASE}-2' / 'hooks' / 'module.py'
        started = time.time()
        result = subprocess.run(
            [sys.executable, str(entry), '--event', 'session-start'],
            input=session_event(session), text=True, capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
        )
        spent = time.time() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertLess(spent, 15, 'копия висела вместо того, чтобы заметить смерть копии 1')
        journal = storage.read_lines('start-context.jsonl')
        self.assertTrue(any('умерла' in line for line in journal), journal)
