"""Ход модуля на событии: режим, requires, след, поиск соседей по каталогу."""

import sys
import tempfile
import unittest
from pathlib import Path

from jarvis import confirm, mode, registry, run
from jarvis.event import Event
from jarvis.events import PRE_TOOL, PROMPT
from jarvis.manifest import Manifest
from jarvis.module import Delivery, Module
from jarvis.response import Ask, Context, Deny, Response
from jarvis.storage import Storage
from jarvis.trace import Trace


def delivered(unified: str, response: Response) -> Delivery:
    """Перевод-заглушка: тесты библиотеки не зависят от конкретного харнеса."""
    return Delivery(payload={'kind': response.kind})


class Talker(Module):
    def handle(self, event, runtime):
        return Context('сказал')


class Denier(Module):
    def handle(self, event, runtime):
        return Deny('нельзя')


class Asker(Module):
    def handle(self, event, runtime):
        if runtime.take_confirmation('снести'):
            return Context('подтверждено')
        runtime.ask_confirmation('снести', 'снеси ветку')
        return Ask(reason='точно?', phrase='снеси ветку', action='снести')


class Broken(Module):
    def handle(self, event, runtime):
        raise RuntimeError('в модуле дефект')


class WrongType(Module):
    def handle(self, event, runtime):
        return 'разрешить'


def make_module_dir(modules_dir: Path, slug: str, header: str = '', lib: dict | None = None) -> Path:
    """Папка модуля в каталоге модулей: шапка и, если надо, lib-часть."""
    folder = modules_dir / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'module.toml').write_text(header or f'slug = "{slug}"\n', encoding='utf-8')
    if lib is not None:
        part = folder / 'lib'
        part.mkdir(exist_ok=True)
        for name, text in lib.items():
            (part / name).write_text(text, encoding='utf-8')
    return folder


class RunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.modules = self.root / 'modules'
        self.modules.mkdir()
        self.storage = Storage(self.root / 'state', 'sess-1')
        self.personal = self.root / 'personal.json'
        self.source = self.root / 'source.json'
        self.module_dir = make_module_dir(self.modules, 'probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.manifest = Manifest(slug='probe', events=(PROMPT,), path=self.module_dir)
        self.event = Event(event=PROMPT, session_id='sess-1', cwd='/w', harness='test', prompt_text='привет')

    def go(self, module: Module, manifest: Manifest | None = None, event=None, module_dir=None):
        return run(
            module,
            manifest or self.manifest,
            event or self.event,
            self.storage,
            translate=delivered,
            module_dir=module_dir or self.module_dir,
            personal_config=self.personal,
            source_config=self.source,
            env={},
        )

    def trace(self) -> list[dict]:
        return Trace(self.storage).read()

    def test_silence_is_written_to_the_trace(self) -> None:
        outcome = self.go(Module())
        self.assertEqual(outcome.response.kind, 'silence')
        self.assertEqual([line['response'] for line in self.trace()], ['silence'])

    def test_answer_is_written_with_its_reason(self) -> None:
        self.go(Denier(), Manifest(slug='probe', events=(PRE_TOOL,), path=self.module_dir))
        line = self.trace()[0]
        self.assertEqual(line['response'], 'deny')
        self.assertEqual(line['reason'], 'нельзя')

    def test_disabled_module_stays_silent_and_says_so_in_the_trace(self) -> None:
        mode.set_session('probe', False, self.storage)
        outcome = self.go(Talker())
        self.assertEqual(outcome.response.kind, 'silence')
        line = self.trace()[0]
        self.assertFalse(line['mode_enabled'])
        self.assertEqual(line['mode_source'], 'session')
        self.assertEqual(line['reason'], 'модуль выключен')

    def test_disabled_module_does_not_stop_the_others(self) -> None:
        mode.set_session('quiet', False, self.storage)
        loud = make_module_dir(self.modules, 'loud')
        outcome = self.go(Talker(), Manifest(slug='loud', events=(PROMPT,), path=loud), module_dir=loud)
        self.assertEqual(outcome.response.kind, 'context')

    def test_autonomy_flag_lands_in_the_trace(self) -> None:
        run(
            Module(),
            self.manifest,
            self.event,
            self.storage,
            translate=delivered,
            module_dir=self.module_dir,
            env={'JARVIS_AUTONOMOUS': '1'},
        )
        self.assertTrue(self.trace()[0]['autonomous'])

    def test_class_name_goes_to_the_class_field(self) -> None:
        self.go(Talker())
        self.assertEqual(self.trace()[0]['module_class'], 'Talker')
        self.assertEqual(self.trace()[0]['module'], 'probe')

    def test_module_without_its_requires_says_what_is_missing_and_stays_quiet(self) -> None:
        manifest = Manifest(slug='probe', events=(PROMPT,), requires=('shell-tree', 'other-*'), path=self.module_dir)
        outcome = self.go(Talker(), manifest)
        self.assertEqual(outcome.response.kind, 'context')
        self.assertIn('shell-tree', outcome.response.text)
        self.assertIn('other-*', outcome.response.text)
        self.assertIn('не найдено из requires', self.trace()[0]['reason'])

    def test_module_with_a_neighbour_it_requires_runs(self) -> None:
        make_module_dir(self.modules, 'shell-tree')
        manifest = Manifest(slug='probe', events=(PROMPT,), requires=('shell-tree',), path=self.module_dir)
        self.assertEqual(self.go(Talker(), manifest).response.text, 'сказал')

    def test_family_mask_is_satisfied_by_an_adapter_next_door(self) -> None:
        make_module_dir(self.modules, 'bash', 'slug = "bash"\nfor = "shell-tree"\n')
        manifest = Manifest(slug='probe', events=(PROMPT,), requires=('shell-tree-*',), path=self.module_dir)
        self.assertEqual(self.go(Talker(), manifest).response.kind, 'context')

    def test_confirmation_is_checked_on_the_prompt_event(self) -> None:
        first = self.go(Asker())
        self.assertEqual(first.response.kind, 'ask')
        self.assertEqual(confirm.pending(self.storage, 'probe')[0]['phrase'], 'снеси ветку')

        confirmed = Event(event=PROMPT, session_id='sess-1', cwd='/w', harness='test', prompt_text='снеси ветку')
        self.assertEqual(self.go(Asker(), event=confirmed).response.text, 'подтверждено')
        self.assertEqual(
            self.go(Asker(), event=confirmed).response.kind,
            'ask',
            'подтверждение действует на одно действие',
        )

    def test_module_error_is_traced_and_not_swallowed(self) -> None:
        with self.assertRaises(RuntimeError):
            self.go(Broken())
        line = self.trace()[0]
        self.assertEqual(line['response'], 'error')
        self.assertIn('в модуле дефект', line['reason'])

    def test_answer_outside_the_unified_format_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            self.go(WrongType())

    def test_answer_outside_the_unified_format_still_writes_a_trace_line(self) -> None:
        # Иначе по следу такое событие неотличимо от хука, который не запускался.
        with self.assertRaises(TypeError):
            self.go(WrongType())
        line = self.trace()[0]
        self.assertEqual(line['response'], 'error')
        self.assertIn('TypeError', line['reason'])
        self.assertFalse(line['delivered'])

    def test_runtime_finds_a_library_neighbour_by_slug(self) -> None:
        make_module_dir(self.modules, 'shell-tree', lib={'__init__.py': 'VALUE = 5\n'})
        manifest = Manifest(slug='probe', events=(PROMPT,), requires=('shell-tree',), path=self.module_dir)

        class Caller(Module):
            def handle(self, event, runtime):
                return Context(str(runtime.library('shell-tree').load().VALUE))

        self.assertEqual(self.go(Caller(), manifest).response.text, '5')


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.modules = Path(self.tmp.name) / 'modules'
        self.modules.mkdir()
        self.caller = make_module_dir(self.modules, 'probe')

    def tearDown(self) -> None:
        for name in [n for n in sys.modules if n.startswith(registry.PARENT_PACKAGE)]:
            del sys.modules[name]

    def test_neighbours_are_read_from_the_modules_directory(self) -> None:
        make_module_dir(self.modules, 'shell-tree')
        self.assertEqual({m.slug for m in registry.neighbours(self.caller)}, {'probe', 'shell-tree'})

    def test_a_folder_without_a_header_is_not_a_module(self) -> None:
        (self.modules / registry.CORE_DIR).mkdir()
        self.assertEqual({m.slug for m in registry.neighbours(self.caller)}, {'probe'})

    def test_library_part_is_found_by_slug(self) -> None:
        make_module_dir(self.modules, 'shell-tree', lib={'__init__.py': 'VALUE = 42\n'})
        found = registry.find('shell-tree', self.caller)
        self.assertEqual(found.slug, 'shell-tree')
        self.assertEqual(found.load().VALUE, 42)

    def test_library_part_is_found_by_a_family_mask(self) -> None:
        make_module_dir(self.modules, 'bash', 'slug = "bash"\nfor = "shell-tree-*"\n', lib={'__init__.py': 'V = 1\n'})
        self.assertEqual(registry.find('shell-tree-*', self.caller).slug, 'bash')

    def test_a_module_that_is_not_there_is_not_found(self) -> None:
        self.assertIsNone(registry.find('nothing', self.caller))

    def test_a_neighbour_without_a_library_part_is_not_found(self) -> None:
        make_module_dir(self.modules, 'shell-tree')
        self.assertIsNone(registry.find('shell-tree', self.caller))

    def test_multi_file_library_part_loads_with_the_ordinary_package_form(self) -> None:
        # `from . import helper` требует, чтобы родительский пакет был заведён.
        make_module_dir(
            self.modules,
            'shell-tree',
            lib={'helper.py': 'VALUE = 7\n', '__init__.py': 'from . import helper\n\nVALUE = helper.VALUE\n'},
        )
        self.assertEqual(registry.find('shell-tree', self.caller).load().VALUE, 7)

    def test_multi_file_library_part_loads_with_the_from_dot_form(self) -> None:
        make_module_dir(
            self.modules,
            'shell-tree',
            lib={'helper.py': 'VALUE = 9\n', '__init__.py': 'from .helper import VALUE\n'},
        )
        self.assertEqual(registry.find('shell-tree', self.caller).load().VALUE, 9)

    def test_a_library_part_that_fails_leaves_nothing_half_loaded(self) -> None:
        make_module_dir(self.modules, 'shell-tree', lib={'__init__.py': 'raise RuntimeError("дефект")\n'})
        with self.assertRaises(RuntimeError):
            registry.find('shell-tree', self.caller).load()
        self.assertNotIn(f'{registry.PARENT_PACKAGE}.shell_tree', sys.modules)


if __name__ == '__main__':
    unittest.main()
