"""Цепочка стартового контекста: сборка у поставщиков, куски и их порядок.

Харнеса тут нет: копии запускаются теми же процессами и с тем же событием, что
запустил бы Claude, а порядок кусков берётся из порядка завершения процессов —
именно так его берёт и сам Claude (замер 16.09.2026).
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from importlib.machinery import SourceFileLoader
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


def load_base():
    """Функции базы без запуска: точка входа под `if __name__`, ядро уже в пути."""
    loader = SourceFileLoader('start_context_claude', str(MODULES_DIR / BASE / 'hooks' / 'module.py'))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class SeamTest(unittest.TestCase):
    """Шов между кусками: не внутри слова и не внутри ссылки.

    Харнес склеивает выводы хуков переносом строки, и шов виден в контексте:
    на потолке ровно по счёту он рвал слова и ссылки `block://…` (замер
    18.09.2026 — 13 разрывов на живом снимке).
    """

    size = 900

    @classmethod
    def setUpClass(cls) -> None:
        cls.base = load_base()

    def pieces(self, text: str, size: int | None = None) -> list[str]:
        cut, truncated = self.base.cut(text, size or self.size, 10_000)
        self.assertFalse(truncated)
        return cut

    def test_a_snapshot_like_text_keeps_every_link_whole(self) -> None:
        # Текст живого снимка: markdown строками, в каждой ссылка block://.
        text = '\n'.join(
            f'- [Раздел {n}](block://aaaaaaaa-{n:04d}-bbbb-cccc-dddddddddddd) — и хвост строки'
            for n in range(400)
        )
        pieces = self.pieces(text)
        self.assertGreater(len(pieces), 10)
        for piece in pieces:
            self.assertLessEqual(len(piece), self.size)
        # Шов не внутри ссылки: у каждого куска, кроме последнего, столько же
        # закрывающих скобок, сколько открытий block://.
        for piece in pieces:
            self.assertEqual(piece.count('block://'), piece.count('dddddddddddd)'))

    def test_the_pieces_glue_back_into_the_very_same_text(self) -> None:
        # Разделитель остаётся в куске, поэтому склейка посимвольно исходная.
        text = '\n'.join(f'строка {n} и ещё немного слов в ней' for n in range(300))
        self.assertEqual(''.join(self.pieces(text)), text)

    def test_a_line_longer_than_the_cap_is_cut_by_a_space(self) -> None:
        text = '## шапка\n\n' + ' '.join('слово' for _ in range(500))
        pieces = self.pieces(text)
        for piece in pieces[:-1]:
            self.assertTrue(piece.endswith(' '), repr(piece[-20:]))
            self.assertLessEqual(len(piece), self.size)
        self.assertEqual(''.join(pieces), text)

    def test_a_word_longer_than_the_cap_is_cut_by_the_cap(self) -> None:
        # Рвать больше нечего: кусок ровно по потолку, и это не дефект.
        text = '## шапка\n\n' + 'я' * (self.size * 3)
        pieces = self.pieces(text)
        self.assertEqual([len(piece) for piece in pieces[:-1]], [self.size] * (len(pieces) - 1))
        self.assertEqual(''.join(pieces), text)

    def test_an_early_line_break_does_not_leave_an_almost_empty_piece(self) -> None:
        # Граница строки годится, только если следующая строка вообще влезает:
        # иначе шов уехал бы к десятому знаку и кусок ушёл бы почти пустым.
        text = '## шапка\n\n' + 'я' * (self.size * 2)
        self.assertEqual(len(self.pieces(text)[0]), self.size)

    def test_no_piece_is_ever_longer_than_the_cap(self) -> None:
        for text in ('', 'коротко', 'а' * self.size, 'а' * (self.size + 1),
                     '\n'.join('строка' for _ in range(5000))):
            for piece in self.pieces(text):
                self.assertLessEqual(len(piece), self.size)

    def test_the_cut_tail_still_fits_the_cap(self) -> None:
        text = '\n'.join(f'строка {n} и ещё немного слов' for n in range(3000))
        pieces, truncated = self.base.cut(text, self.size, 3)
        self.assertTrue(truncated)
        self.assertEqual(len(pieces), 3)
        for piece in pieces:
            self.assertLessEqual(len(piece), self.size)


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
                 '--harness', 'claude', '--event', 'session-start', '--event', 'after-compact'],
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
            # Кусок только короче потолка: шов ищется по границе строки.
            self.assertLessEqual(len(piece), CHUNK)

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
                    [sys.executable, str(entry), '--harness', 'claude', '--event', 'session-start'],
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


def shrink_bootstrap(settings_root: Path, seconds: float) -> None:
    """Укоротить единственный срок модуля в разложенных копиях.

    Он покрывает только появление маркера «жива», то есть ошибку раскладки.
    Сжав его до секунды, тест отличает «ждём загрузку без потолка» от «ждём по
    таймеру»: с потолком на загрузку такой прогон развалился бы.
    """
    for entry in (settings_root / 'jarvis' / 'modules').glob('*/hooks/module.py'):
        text = entry.read_text(encoding='utf-8')
        entry.write_text(
            text.replace('BOOTSTRAP_SECONDS = 60.0', f'BOOTSTRAP_SECONDS = {seconds}'),
            encoding='utf-8',
        )


class SlowGatherTest(unittest.TestCase):
    """Сбор идёт долго: копии ждут его, а не сдаются по таймеру."""

    copies = 3
    # Сбор заведомо дольше срока, оставшегося в модуле: если бы потолок на
    # загрузку существовал, копии сдались бы.
    gather_seconds = 5
    bootstrap = 1.0

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
        shrink_bootstrap(cls.settings_root, cls.bootstrap)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def entry(self, number: int) -> Path:
        return self.settings_root / 'jarvis' / 'modules' / f'{BASE}-{number}' / 'hooks' / 'module.py'

    def run_copies(self, session: str, numbers) -> dict:
        payload = session_event(session)
        numbers = list(numbers)
        with ThreadPoolExecutor(max_workers=len(numbers)) as pool:
            results = list(pool.map(
                lambda number: subprocess.run(
                    [sys.executable, str(self.entry(number)), '--harness', 'claude',
                     '--event', 'session-start'],
                    input=payload, text=True, capture_output=True,
                    env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
                ),
                numbers,
            ))
        printed = {}
        for number, result in zip(numbers, results):
            self.assertEqual(result.returncode, 0, f'копия {number}: {result.stderr}')
            if result.stdout.strip():
                printed[number] = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        return printed

    def test_a_dead_neighbour_does_not_stall_the_chain(self) -> None:
        # Копию 2 не запускаем вовсе: копия 3 обязана напечатать свой кусок, а
        # не ждать соседа, которого нет. Теряется только его кусок.
        printed = self.run_copies('skip-two', [1, 3])
        self.assertEqual(sorted(printed), [1, 3])

    def test_every_copy_waits_out_a_slow_gather(self) -> None:
        printed = self.run_copies('slow', range(1, self.copies + 1))
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
        shrink_bootstrap(cls.settings_root, 1.0)

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
            [sys.executable, str(entry), '--harness', 'claude', '--event', 'session-start'],
            input=session_event(session), text=True, capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
        )
        spent = time.time() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertLess(spent, 15, 'копия висела вместо того, чтобы заметить смерть копии 1')
        journal = storage.read_lines('start-context.jsonl')
        self.assertTrue(any('умерла' in line for line in journal), journal)


class SweepTest(unittest.TestCase):
    """Копия 1 убирает мусор прошлых прогонов своего события — и только его."""

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
            f'harness = "claude"\ncopies = 2\n',
            encoding='utf-8',
        )
        data = source / 'tiny' / 'data'
        data.mkdir(parents=True)
        (source / 'tiny' / 'module.toml').write_text(
            'slug = "tiny"\nfor = ["start-context-*"]\n', encoding='utf-8'
        )
        (data / 'hello.md').write_text('маленький текст', encoding='utf-8')
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

    def storage(self, session: str) -> Storage:
        return Storage(self.home / '.local' / 'state' / 'jarvis', session)

    def test_old_generations_go_and_the_neighbours_stay(self) -> None:
        session = 'sweep-me'
        storage = self.storage(session)
        old = time.time() - 3600
        stale = {
            'start-context-session-start.json': {'generation': 'старое', 'at': old},
            'start-context-ready-session-start.json': {'generation': 'старое', 'at': old},
            'start-context-alive-session-start-7.json': {'generation': 'старое', 'at': old},
            'start-context-printed-session-start-7.json': {'generation': 'старое', 'at': old},
        }
        # Чужое событие час назад — это, возможно, живой прогон: не трогаем.
        keep = {
            'start-context-alive-after-compact-3.json': {'generation': 'старое', 'at': old},
            'start-context-after-compact.json': {'generation': 'старое', 'at': old},
        }
        for name, body in {**stale, **keep}.items():
            storage.write_json(name, body)
        storage.append_line('trace.jsonl', '{}')

        entry = self.settings_root / 'jarvis' / 'modules' / f'{BASE}-1' / 'hooks' / 'module.py'
        result = subprocess.run(
            [sys.executable, str(entry), '--harness', 'claude', '--event', 'session-start', '--event', 'after-compact'],
            input=session_event(session), text=True, capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        for name in ('start-context-alive-session-start-7.json',
                     'start-context-printed-session-start-7.json'):
            self.assertFalse(storage.path(name).exists(), f'{name} остался')
        for name in keep:
            self.assertTrue(storage.path(name).exists(),
                             f'{name} убрали, а он чужого события и свежий')
        self.assertTrue(storage.path('trace.jsonl').exists(), 'уборка задела не свои файлы')

        # Состояние и готовность этого прогона на месте и нового поколения.
        state = storage.read_json('start-context-session-start.json')
        ready = storage.read_json('start-context-ready-session-start.json')
        self.assertNotEqual(state['generation'], 'старое')
        self.assertEqual(state['generation'], ready['generation'])
        # Четыре: состояние, готовность и пара маркеров копии, которой больше нет.
        self.assertTrue(any('убрано файлов прошлых прогонов: 4' in line
                            for line in storage.read_lines('start-context.jsonl')),
                        storage.read_lines('start-context.jsonl'))

    def test_traces_of_other_events_go_after_a_day(self) -> None:
        # В одной сессии session-start бывает раз, а after-compact — сколько
        # угодно раз после него. Без этого следы старта пролежали бы в зоне до
        # конца сессии: поколение чужого события нам ничего не говорит, и
        # единственный признак, что прогон давно кончился, — возраст.
        session = 'sweep-day'
        storage = self.storage(session)
        long_ago = time.time() - 2 * 24 * 3600
        yesterday = {
            'start-context-session-start.json': {'generation': 'позавчерашнее', 'at': long_ago},
            'start-context-alive-session-start-4.json':
                {'generation': 'позавчерашнее', 'at': long_ago},
        }
        fresh = {
            'start-context-printed-session-start-1.json':
                {'generation': 'сегодняшнее', 'at': time.time() - 3600},
        }
        for name, body in {**yesterday, **fresh}.items():
            storage.write_json(name, body)

        entry = self.settings_root / 'jarvis' / 'modules' / f'{BASE}-1' / 'hooks' / 'module.py'
        result = subprocess.run(
            [sys.executable, str(entry), '--harness', 'claude', '--event', 'after-compact'],
            input=session_event(session, source='compact'), text=True, capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        for name in yesterday:
            self.assertFalse(storage.path(name).exists(),
                             f'{name} чужого события и старше суток, а остался')
        for name in fresh:
            self.assertTrue(storage.path(name).exists(),
                            f'{name} моложе суток, трогать его было нельзя')

    def test_what_this_run_writes_survives_the_sweep(self) -> None:
        # Маркер соседа этого прогона ещё без поколения: уборка обязана его
        # пощадить, иначе снесёт то, что сосед как раз собирается переписать.
        session = 'sweep-now'
        storage = self.storage(session)
        storage.write_json('start-context-alive-session-start-2.json',
                           {'generation': None, 'pid': os.getpid(), 'at': time.time()})
        entry = self.settings_root / 'jarvis' / 'modules' / f'{BASE}-1' / 'hooks' / 'module.py'
        subprocess.run(
            [sys.executable, str(entry), '--harness', 'claude', '--event', 'session-start'],
            input=session_event(session), text=True, capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)},
        )
        self.assertTrue(storage.path('start-context-alive-session-start-2.json').exists(),
                        'уборка снесла маркер живого соседа этого прогона')
