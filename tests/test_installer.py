"""Установщик на временном каталоге настроек."""

import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from jarvis import install as install_reader

from . import INSTALLER, REPO_ROOT


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
        self.modules_dir = self.root / 'modules'
        self.state_dir = self.root / 'state'
        self.modules_dir.mkdir()

    def add_module(self, slug: str, header: str, parts: dict[str, dict[str, str]] | None = None) -> Path:
        folder = self.modules_dir / slug
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
            '--modules', str(self.modules_dir),
            '--lib', str(REPO_ROOT / 'lib'),
            '--state-dir', str(self.state_dir),
            '--personal-config', str(self.root / 'personal.json'),
            '--python', '/usr/bin/python3',
            *extra,
        ]
        return installer.install(installer.parse_args(argv))

    def settings(self) -> dict:
        return json.loads((self.settings_root / 'settings.json').read_text(encoding='utf-8'))

    def jarvis_handlers(self, claude_event: str) -> list[dict]:
        groups = self.settings()['hooks'].get(claude_event, [])
        return [h for group in groups for h in group['hooks'] if installer.is_jarvis_line(h)]

    # --- строка на модуль и событие ---

    def test_one_line_per_module_and_event(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt", "pre-tool"]\n')
        report = self.run_installer()
        self.assertEqual(sorted(report.lines), [('probe', 'pre-tool'), ('probe', 'prompt')])
        self.assertEqual(len(self.jarvis_handlers('UserPromptSubmit')), 1)
        self.assertEqual(len(self.jarvis_handlers('PreToolUse')), 1)

    def test_line_names_the_module_the_event_and_the_install_root(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        args = self.jarvis_handlers('UserPromptSubmit')[0]['args']
        self.assertIn('--module', args)
        self.assertEqual(args[args.index('--module') + 1], 'probe')
        self.assertEqual(args[args.index('--event') + 1], 'prompt')
        self.assertEqual(args[args.index('--install-root') + 1], str(self.settings_root))
        self.assertTrue(args[0].endswith(installer.HOOK_ENTRY))

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
        leftovers = [p.name for p in self.settings_root.iterdir() if '.tmp-' in p.name]
        self.assertEqual(leftovers, [])

    # --- сборка библиотеки ---

    def test_library_is_assembled_once(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        lib_dir = install_reader.jarvis_root(self.settings_root) / 'lib'
        self.assertTrue((lib_dir / 'jarvis' / '__init__.py').exists())
        self.assertTrue((lib_dir / installer.HOOK_ENTRY).exists())
        cores = list(install_reader.jarvis_root(self.settings_root).rglob('jarvis/storage.py'))
        self.assertEqual(len(cores), 1, 'копий ядра быть не должно')

    def test_lib_part_of_a_module_joins_the_assembled_library(self) -> None:
        self.add_module(
            'shell-tree',
            'slug = "shell-tree"\n',
            {'lib': {'__init__.py': 'VALUE = 1\n'}},
        )
        self.run_installer()
        installed = install_reader.load(self.settings_root)
        self.assertEqual(installed.lib_parts, {'shell-tree': 'shell-tree'})
        self.assertTrue((installed.lib_dir / 'shell-tree' / '__init__.py').exists())

    def test_bytecode_is_not_carried_into_the_install(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        self.assertEqual(list(self.settings_root.rglob('__pycache__')), [])

    # --- части модуля ---

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
        jarvis_root = install_reader.jarvis_root(self.settings_root)
        self.assertTrue((jarvis_root / 'modules' / 'probe' / 'hooks' / 'module.py').exists())
        self.assertTrue((jarvis_root / 'modules' / 'probe' / 'data' / 'list.json').exists())
        self.assertTrue((jarvis_root / 'lib' / 'probe' / '__init__.py').exists())
        self.assertTrue((self.settings_root / 'skills' / 'probe' / 'SKILL.md').exists())
        self.assertTrue((self.settings_root / 'agents' / 'probe' / 'reviewer.md').exists())
        self.assertTrue((self.settings_root / 'rules' / 'probe' / 'rule.md').exists())

    def test_header_travels_with_the_module(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        module_dir = install_reader.load(self.settings_root).module_dir('probe')
        self.assertTrue((module_dir / 'module.toml').exists())

    def test_parts_are_installed_independently(self) -> None:
        self.add_module('only-skill', 'slug = "only-skill"\n', {'skill': {'SKILL.md': '# один скилл\n'}})
        report = self.run_installer()
        self.assertEqual([part for _, part, _ in report.parts], ['skill'])
        self.assertEqual(report.lines, [])

    def test_harness_paths_are_substituted_for_placeholders(self) -> None:
        self.add_module(
            'probe',
            'slug = "probe"\nevents = ["prompt"]\n',
            {'skill': {'SKILL.md': 'Библиотека: {{JARVIS_LIB}}\nСостояние: {{JARVIS_STATE}}\nПроект: {{HARNESS_PROJECT_DIR}}\n'}},
        )
        self.run_installer()
        text = (self.settings_root / 'skills' / 'probe' / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn(str(install_reader.jarvis_root(self.settings_root) / 'lib'), text)
        self.assertIn(str(self.state_dir), text)
        self.assertIn('${CLAUDE_PROJECT_DIR}', text)
        self.assertNotIn('{{', text)

    # --- предупреждения ---

    def test_missing_requirement_is_reported_at_install_time(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\nrequires = ["shell-tree"]\n')
        report = self.run_installer()
        self.assertTrue(any('shell-tree' in w for w in report.warnings))

    def test_a_satisfied_requirement_is_not_reported(self) -> None:
        self.add_module('probe', 'slug = "probe"\nrequires = ["shell-tree-*"]\n')
        self.add_module('bash', 'slug = "bash"\nfor = "shell-tree"\n')
        self.assertEqual(self.run_installer().warnings, [])

    def test_duplicate_slug_is_rejected(self) -> None:
        self.add_module('one', 'slug = "same"\n')
        self.add_module('two', 'slug = "same"\n')
        with self.assertRaises(ValueError):
            self.run_installer()

    def test_dry_run_changes_nothing(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer('--dry-run')
        self.assertFalse((self.settings_root / 'settings.json').exists())

    def test_install_manifest_records_what_the_library_needs(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\nrequires = []\n')
        self.run_installer()
        installed = install_reader.load(self.settings_root)
        self.assertEqual(installed.state_dir, self.state_dir)
        self.assertEqual(installed.source_config, self.modules_dir / installer.SOURCE_CONFIG_NAME)
        self.assertEqual(installed.modules['probe']['events'], ['prompt'])


class UnknownEventWarningTest(unittest.TestCase):
    """Событие вне каталога харнеса режется ещё разбором шапки, а поверх —
    предупреждением установщика для события, которое в каталог войдёт позже."""

    def test_event_unknown_to_the_harness_is_reported(self) -> None:
        report = installer.Report()
        manifest = type('M', (), {'slug': 'probe', 'events': ('worktree-create',), 'requires': ()})()
        installer.check_events([manifest], report)
        self.assertTrue(any('claude' in w and 'worktree-create' in w for w in report.warnings))


if __name__ == '__main__':
    unittest.main()
