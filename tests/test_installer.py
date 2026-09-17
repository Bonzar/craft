"""Установщик на временном каталоге настроек."""

import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from . import CORE_SOURCE, INSTALLER


def load_installer():
    """Установщик — исполняемый файл без расширения, грузим его по пути."""
    spec = importlib.util.spec_from_loader(
        'jarvis_install',
        importlib.machinery.SourceFileLoader('jarvis_install', str(INSTALLER)),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules['jarvis_install'] = module
    spec.loader.exec_module(module)
    return module


installer = load_installer()


class InstallerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings_root = self.root / 'settings'
        self.source = self.root / 'source'
        self.state_dir = self.root / 'state'
        self.source.mkdir()

    def add_module(self, slug: str, header: str, parts: dict[str, dict[str, str]] | None = None) -> Path:
        folder = self.source / slug
        folder.mkdir()
        (folder / 'module.toml').write_text(header, encoding='utf-8')
        for part, files in (parts or {}).items():
            part_dir = folder / part
            part_dir.mkdir()
            for name, text in files.items():
                (part_dir / name).write_text(text, encoding='utf-8')
        return folder

    def run_installer(self, *extra: str):
        argv = [
            '--settings-dir', str(self.settings_root),
            '--modules', str(self.source),
            '--core', str(CORE_SOURCE),
            '--state-dir', str(self.state_dir),
            '--python', '/usr/bin/python3',
            *extra,
        ]
        return installer.install(installer.parse_args(argv))

    def modules_root(self) -> Path:
        return installer.modules_root(self.settings_root)

    def settings(self) -> dict:
        return json.loads((self.settings_root / 'settings.json').read_text(encoding='utf-8'))

    def jarvis_handlers(self, claude_event: str) -> list[dict]:
        groups = self.settings()['hooks'].get(claude_event, [])
        return [h for group in groups for h in group['hooks'] if installer.is_jarvis_line(h, self.modules_root())]

    # --- строка на модуль и событие ---

    def test_one_line_per_module_and_event(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt", "pre-tool"]\n')
        report = self.run_installer()
        self.assertEqual(sorted(report.lines), [('probe', 'pre-tool'), ('probe', 'prompt')])
        self.assertEqual(len(self.jarvis_handlers('UserPromptSubmit')), 1)
        self.assertEqual(len(self.jarvis_handlers('PreToolUse')), 1)

    def test_line_runs_the_module_own_file_and_names_the_event(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n', {'hooks': {'module.py': ''}})
        self.run_installer()
        args = self.jarvis_handlers('UserPromptSubmit')[0]['args']
        self.assertEqual(args[0], str(self.modules_root() / 'probe' / 'hooks' / 'module.py'))
        self.assertEqual(args[1:], ['--event', 'prompt'])

    def test_line_carries_no_install_root(self) -> None:
        # Модуль находит ядро и соседей от своего файла, аргумент ему не нужен.
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        self.assertNotIn('--install-root', self.jarvis_handlers('UserPromptSubmit')[0]['args'])

    def test_reinstall_does_not_leave_two_paths_to_one_module(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        self.run_installer()
        self.assertEqual(len(self.jarvis_handlers('UserPromptSubmit')), 1)

    def test_foreign_hooks_survive_the_install(self) -> None:
        self.settings_root.mkdir(parents=True)
        (self.settings_root / 'settings.json').write_text(
            json.dumps(
                {
                    'model': 'opus',
                    'hooks': {'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': 'echo чужой'}]}]},
                }
            ),
            encoding='utf-8',
        )
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        settings = self.settings()
        self.assertEqual(settings['model'], 'opus')
        commands = [h.get('command') for group in settings['hooks']['UserPromptSubmit'] for h in group['hooks']]
        self.assertIn('echo чужой', commands)

    def test_previous_settings_are_kept_as_a_copy(self) -> None:
        self.settings_root.mkdir(parents=True)
        (self.settings_root / 'settings.json').write_text('{"model": "opus"}', encoding='utf-8')
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        backup = self.settings_root / ('settings.json' + installer.BACKUP_SUFFIX)
        self.assertEqual(json.loads(backup.read_text(encoding='utf-8')), {'model': 'opus'})

    def test_write_leaves_no_temporary_file(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        self.assertEqual([p.name for p in self.settings_root.iterdir() if '.tmp-' in p.name], [])

    # --- раскладка ---

    def test_core_lands_inside_every_module(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.add_module('other', 'slug = "other"\n')
        self.run_installer()
        for slug in ('probe', 'other'):
            self.assertTrue((self.modules_root() / slug / '_core' / 'jarvis' / 'storage.py').is_file(), slug)
        self.assertTrue((self.modules_root() / 'probe' / 'module.toml').is_file())

    def test_the_set_of_modules_holds_only_modules(self) -> None:
        # Ядро рядом с модулями больше не лежит: набор для шеринга однороден.
        self.add_module('probe', 'slug = "probe"\n')
        self.run_installer()
        self.assertEqual(sorted(p.name for p in self.modules_root().iterdir() if p.is_dir()), ['probe'])

    def test_core_copies_are_independent_per_module(self) -> None:
        self.add_module('probe', 'slug = "probe"\n')
        self.add_module('other', 'slug = "other"\n')
        self.run_installer()
        marker = self.modules_root() / 'probe' / '_core' / 'jarvis' / 'marker.py'
        marker.write_text('V = 1\n', encoding='utf-8')
        self.assertFalse((self.modules_root() / 'other' / '_core' / 'jarvis' / 'marker.py').exists())

    def test_library_part_stays_inside_its_own_module(self) -> None:
        self.add_module('shell-tree', 'slug = "shell-tree"\n', {'lib': {'__init__.py': 'VALUE = 1\n'}})
        self.run_installer()
        self.assertTrue((self.modules_root() / 'shell-tree' / 'lib' / '__init__.py').is_file())

    def test_each_part_goes_where_it_is_read(self) -> None:
        self.add_module(
            'probe',
            'slug = "probe"\nevents = ["prompt"]\n',
            {
                'hooks': {'module.py': 'x = 1\n'},
                'skill': {'SKILL.md': '# скилл\n'},
                'agents': {'reviewer.md': '# агент\n'},
                'rules': {'rule.md': '# правило\n'},
                'data': {'list.json': '[]\n'},
                'lib': {'__init__.py': '\n'},
            },
        )
        self.run_installer()
        for path in (
            self.modules_root() / 'probe' / 'hooks' / 'module.py',
            self.modules_root() / 'probe' / 'data' / 'list.json',
            self.modules_root() / 'probe' / 'lib' / '__init__.py',
            self.settings_root / 'skills' / 'probe' / 'SKILL.md',
            self.settings_root / 'agents' / 'probe' / 'reviewer.md',
            self.settings_root / 'rules' / 'probe' / 'rule.md',
        ):
            self.assertTrue(path.is_file(), path)

    def test_parts_are_installed_independently(self) -> None:
        self.add_module('only-skill', 'slug = "only-skill"\n', {'skill': {'SKILL.md': '# один скилл\n'}})
        report = self.run_installer()
        self.assertEqual([part for slug, part, _ in report.parts if slug == 'only-skill'], ['skill'])
        self.assertEqual(report.lines, [])

    def test_bytecode_is_not_carried_into_the_install(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        self.assertEqual(list(self.settings_root.rglob('__pycache__')), [])

    def test_harness_paths_are_substituted_for_placeholders(self) -> None:
        self.add_module(
            'probe',
            'slug = "probe"\nevents = ["prompt"]\n',
            {'skill': {'SKILL.md': 'Ядро: {{JARVIS_CORE}}\nСостояние: {{JARVIS_STATE}}\nПроект: {{HARNESS_PROJECT_DIR}}\n'}},
        )
        self.run_installer()
        text = (self.settings_root / 'skills' / 'probe' / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn(str(self.modules_root() / 'probe' / '_core'), text)
        self.assertIn(str(self.state_dir), text)
        self.assertIn('${CLAUDE_PROJECT_DIR}', text)
        self.assertNotIn('{{', text)

    def test_source_mode_config_travels_with_the_modules(self) -> None:
        self.add_module('probe', 'slug = "probe"\n')
        (self.source / 'modules.json').write_text('{"probe": false}', encoding='utf-8')
        self.run_installer()
        installed = self.modules_root() / 'modules.json'
        self.assertEqual(json.loads(installed.read_text(encoding='utf-8')), {'probe': False})

    # --- уборка того, чего в источнике больше нет ---

    def test_a_part_removed_from_the_source_is_removed_from_the_install(self) -> None:
        folder = self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n', {'skill': {'SKILL.md': '# старый\n'}})
        self.run_installer()
        skill = self.settings_root / 'skills' / 'probe'
        self.assertTrue(skill.is_dir())
        shutil.rmtree(folder / 'skill')
        report = self.run_installer()
        self.assertFalse(skill.exists())
        self.assertIn(str(skill), report.removed)

    def test_a_module_removed_from_the_source_leaves_nothing_behind(self) -> None:
        folder = self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n', {'skill': {'SKILL.md': '# старый\n'}})
        self.run_installer()
        shutil.rmtree(folder)
        self.add_module('other', 'slug = "other"\n')
        self.run_installer()
        self.assertFalse((self.settings_root / 'skills' / 'probe').exists())
        self.assertFalse((self.modules_root() / 'probe').exists())
        self.assertEqual(self.jarvis_handlers('UserPromptSubmit'), [])

    def test_the_users_own_skills_are_not_touched(self) -> None:
        own = self.settings_root / 'skills' / 'my-own'
        own.mkdir(parents=True)
        (own / 'SKILL.md').write_text('# моё\n', encoding='utf-8')
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': '# модульный\n'}})
        self.run_installer()
        self.run_installer()
        self.assertTrue((own / 'SKILL.md').is_file())

    def test_a_part_that_stayed_is_not_removed(self) -> None:
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': '# на месте\n'}})
        self.run_installer()
        report = self.run_installer()
        self.assertEqual(report.removed, [])
        self.assertTrue((self.settings_root / 'skills' / 'probe' / 'SKILL.md').is_file())

    # --- предупреждения ---

    def test_missing_requirement_is_reported_at_install_time(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\nrequires = ["shell-tree"]\n')
        report = self.run_installer()
        self.assertTrue(any('shell-tree' in w for w in report.warnings))

    def test_a_satisfied_requirement_is_not_reported(self) -> None:
        self.add_module('probe', 'slug = "probe"\nrequires = ["shell-tree-*"]\n')
        self.add_module('bash', 'slug = "bash"\nfor = "shell-tree-*"\n')
        self.assertEqual(self.run_installer().warnings, [])

    def test_duplicate_slug_is_rejected(self) -> None:
        self.add_module('one', 'slug = "same"\n')
        self.add_module('two', 'slug = "same"\n')
        with self.assertRaises(ValueError):
            self.run_installer()

    def test_unsafe_slug_is_rejected_before_any_filesystem_work(self) -> None:
        self.add_module('evil', 'slug = "../../outside"\n')
        with self.assertRaises(ValueError):
            self.run_installer()
        self.assertFalse(self.settings_root.exists())

    def test_an_uppercase_slug_is_rejected(self) -> None:
        # На macOS файловая система регистронезависима: «Foo» столкнётся с «foo».
        self.add_module('upper', 'slug = "Probe"\n')
        with self.assertRaises(ValueError):
            self.run_installer()

    def test_dry_run_changes_nothing(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer('--dry-run')
        self.assertFalse(self.settings_root.exists())

    def test_ledger_records_what_was_installed(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n', {'skill': {'SKILL.md': '#\n'}})
        self.run_installer()
        ledger = json.loads(
            (self.modules_root().parent / installer.LEDGER_FILE).read_text(encoding='utf-8')
        )
        self.assertEqual(ledger['probe']['events'], ['prompt'])
        self.assertIn('skill', ledger['probe']['parts'])


class UnknownEventWarningTest(unittest.TestCase):
    """Событие вне каталога харнеса режется разбором шапки, а поверх —
    предупреждением установщика для события, которое в каталог войдёт позже."""

    def test_event_unknown_to_the_harness_is_reported(self) -> None:
        report = installer.Report()
        manifest = type('M', (), {'slug': 'probe', 'events': ('worktree-create',), 'requires': ()})()
        installer.check_events([manifest], report)
        self.assertTrue(any('claude' in w and 'worktree-create' in w for w in report.warnings))


if __name__ == '__main__':
    unittest.main()
