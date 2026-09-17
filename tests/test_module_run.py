"""Ход модуля на событии: режим, requires, след, поиск библиотечного модуля."""

import json
import tempfile
import unittest
from pathlib import Path

from jarvis import confirm, mode, registry, run
from jarvis.event import Event
from jarvis.events import PRE_TOOL, PROMPT
from jarvis.install import Install
from jarvis.manifest import Manifest
from jarvis.module import Delivery, Module
from jarvis.response import Ask, Context, Deny, Response, Silence
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


class RunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.storage = Storage(self.root / 'state', 'sess-1')
        self.personal = self.root / 'personal.json'
        self.source = self.root / 'source.json'
        self.manifest = Manifest(slug='probe', events=(PROMPT,))
        self.event = Event(event=PROMPT, session_id='sess-1', cwd='/w', harness='test', prompt_text='привет')

    def go(self, module: Module, manifest: Manifest | None = None, install=None, event=None):
        return run(
            module,
            manifest or self.manifest,
            event or self.event,
            self.storage,
            translate=delivered,
            install=install,
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
        self.go(Denier(), Manifest(slug='probe', events=(PRE_TOOL,)))
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
        outcome = self.go(Talker(), Manifest(slug='loud', events=(PROMPT,)))
        self.assertEqual(outcome.response.kind, 'context')

    def test_autonomy_flag_lands_in_the_trace(self) -> None:
        run(
            Module(),
            self.manifest,
            self.event,
            self.storage,
            translate=delivered,
            env={'JARVIS_AUTONOMOUS': '1'},
        )
        self.assertTrue(self.trace()[0]['autonomous'])

    def test_class_name_goes_to_the_class_field(self) -> None:
        self.go(Talker())
        self.assertEqual(self.trace()[0]['module_class'], 'Talker')
        self.assertEqual(self.trace()[0]['module'], 'probe')

    def test_module_without_its_requires_says_what_is_missing_and_stays_quiet(self) -> None:
        manifest = Manifest(slug='probe', events=(PROMPT,), requires=('shell-tree', 'other-*'))
        outcome = self.go(Talker(), manifest, install=self.install_with({}))
        self.assertEqual(outcome.response.kind, 'context')
        self.assertIn('shell-tree', outcome.response.text)
        self.assertIn('other-*', outcome.response.text)
        self.assertIn('не найдено из requires', self.trace()[0]['reason'])

    def test_module_with_its_requires_runs(self) -> None:
        manifest = Manifest(slug='probe', events=(PROMPT,), requires=('shell-tree',))
        install = self.install_with({'shell-tree': {'events': [], 'requires': [], 'for': None}})
        self.assertEqual(self.go(Talker(), manifest, install=install).response.text, 'сказал')

    def test_family_mask_is_satisfied_by_an_adapter(self) -> None:
        manifest = Manifest(slug='probe', events=(PROMPT,), requires=('shell-tree-*',))
        install = self.install_with({'bash-adapter': {'events': [], 'requires': [], 'for': 'shell-tree'}})
        self.assertEqual(self.go(Talker(), manifest, install=install).response.kind, 'context')

    def test_confirmation_is_checked_on_the_prompt_event(self) -> None:
        first = self.go(Asker())
        self.assertEqual(first.response.kind, 'ask')
        self.assertEqual(confirm.pending(self.storage, 'probe')[0]['phrase'], 'снеси ветку')

        confirmed = Event(event=PROMPT, session_id='sess-1', cwd='/w', harness='test', prompt_text='снеси ветку')
        second = self.go(Asker(), event=confirmed)
        self.assertEqual(second.response.text, 'подтверждено')

        third = self.go(Asker(), event=confirmed)
        self.assertEqual(third.response.kind, 'ask', 'подтверждение действует на одно действие')

    def test_module_error_is_traced_and_not_swallowed(self) -> None:
        with self.assertRaises(RuntimeError):
            self.go(Broken())
        line = self.trace()[0]
        self.assertEqual(line['response'], 'error')
        self.assertIn('в модуле дефект', line['reason'])

    def test_answer_outside_the_unified_format_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            self.go(WrongType())

    def install_with(self, modules: dict) -> Install:
        return Install(
            settings_root=self.root / 'settings',
            state_dir=self.root / 'state',
            personal_config=self.personal,
            source_config=self.source,
            lib_parts={},
            modules=modules,
        )


class RegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.install = Install(
            settings_root=self.root,
            state_dir=self.root / 'state',
            personal_config=None,
            source_config=None,
            lib_parts={'shell-tree': 'shell-tree'},
            modules={},
        )

    def test_library_module_is_found_by_its_slug(self) -> None:
        part = self.install.lib_dir / 'shell-tree'
        part.mkdir(parents=True)
        (part / '__init__.py').write_text('VALUE = 42\n', encoding='utf-8')
        found = registry.find('shell-tree', self.install)
        self.assertEqual(found.slug, 'shell-tree')
        self.assertEqual(found.load().VALUE, 42)

    def test_module_that_was_not_installed_is_not_found(self) -> None:
        self.assertIsNone(registry.find('nothing', self.install))

    def test_recorded_but_absent_part_is_not_found(self) -> None:
        self.assertIsNone(registry.find('shell-tree', self.install))


if __name__ == '__main__':
    unittest.main()
