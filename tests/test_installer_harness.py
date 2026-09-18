"""Харнес, копии и раскладка Codex: то, чего у установщика не было до этапа 3."""

import json
import os
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from . import CORE_SOURCE
from .test_installer import installer

FAKE_CODEX = textwrap.dedent('''
    #!/usr/bin/env python3
    """Подставной app-server Codex: отвечает на hooks/list и пишет, что у него просили.

    Настоящий Codex в тестах не нужен и в CI его нет, а разговор установщика с
    ним проверить надо: ключ доверия, хеш и то, что пишется ровно две записи на
    строку хука.
    """
    import json, os, re, sys

    config = os.environ['FAKE_CONFIG']
    journal = os.environ['FAKE_JOURNAL']
    commands = re.findall(r'^command = "(.*)"$', open(config, encoding='utf-8').read(), re.M)
    hooks = [
        {
            'key': f'{config}:session_start:{i}:0',
            'eventName': 'sessionStart',
            'handlerType': 'command',
            'command': command.replace('\\\\"', '"').replace('\\\\\\\\', '\\\\'),
            'sourcePath': config,
            'currentHash': f'sha256:hash-{i}',
            'trustStatus': 'untrusted',
            'enabled': True,
        }
        for i, command in enumerate(commands)
    ]
    written = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        if message.get('method') == 'initialize':
            print(json.dumps({'id': message['id'], 'result': {'codexHome': config}}), flush=True)
        elif message.get('method') == 'hooks/list':
            print(json.dumps({'id': message['id'], 'result': {'data': [{'cwd': '.', 'hooks': hooks,
                                                                        'warnings': [], 'errors': []}]}}), flush=True)
        elif message.get('method') == 'config/value/write':
            written.append(message['params'])
            # Журнал пишется ДО ответа и атомарно: установщик убивает
            # app-server сразу, как получил последний ответ, — настоящий Codex
            # тоже успевает записать конфиг раньше, чем отвечает.
            tmp = journal + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(written, handle, ensure_ascii=False)
            os.replace(tmp, journal)
            print(json.dumps({'id': message['id'], 'result': {'status': 'ok'}}), flush=True)
''').lstrip()


class HarnessAndCopiesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings_root = self.root / 'settings'
        self.source = self.root / 'source'
        self.state_dir = self.root / 'state'
        self.source.mkdir()
        self.journal = self.root / 'fake-codex.json'
        self.fake_cli = self.root / 'fake-codex'
        self.fake_cli.write_text(FAKE_CODEX, encoding='utf-8')
        self.fake_cli.chmod(0o755)
        for name, value in (('FAKE_CONFIG', str(self.settings_root / 'config.toml')),
                            ('FAKE_JOURNAL', str(self.journal))):
            previous = os.environ.get(name)
            os.environ[name] = value
            self.addCleanup(lambda n=name, v=previous: os.environ.pop(n) if v is None else os.environ.__setitem__(n, v))

    def add_module(self, slug: str, header: str, parts=None) -> Path:
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

    def config_toml(self) -> str:
        return (self.settings_root / 'config.toml').read_text(encoding='utf-8')

    def codex_stanza(self, harness_event: str) -> str:
        """Тело строки хука одного события Codex: от его заголовка до следующего."""
        chunks = self.config_toml().split('[[hooks.')
        bodies = [chunk for chunk in chunks if chunk.startswith(f'{harness_event}.hooks]]')]
        self.assertEqual(len(bodies), 1, f'{harness_event}: строк не одна — {len(bodies)}')
        return bodies[0]

    def agents_md(self) -> str:
        path = self.settings_root / 'AGENTS.md'
        return path.read_text(encoding='utf-8') if path.is_file() else ''

    # --- харнес ---

    def test_a_module_of_another_harness_is_not_installed(self) -> None:
        self.add_module('only-codex', 'slug = "only-codex"\nharness = "codex"\n')
        self.add_module('everywhere', 'slug = "everywhere"\n')
        report = self.run_installer()
        self.assertEqual(report.installed, ['everywhere'])
        self.assertFalse((self.modules_root() / 'only-codex').exists())

    def test_a_module_without_the_field_goes_into_both(self) -> None:
        self.add_module('everywhere', 'slug = "everywhere"\n')
        self.run_installer()
        self.run_installer('--harness', 'codex', '--no-trust')
        self.assertTrue((self.modules_root() / 'everywhere').is_dir())

    def test_the_default_harness_is_claude(self) -> None:
        self.assertEqual(installer.parse_args([]).harness, 'claude')

    # --- копии ---

    def test_copies_make_numbered_folders_and_slugs(self) -> None:
        self.add_module('base', 'slug = "base"\nevents = ["session-start"]\ncopies = 3\n')
        report = self.run_installer()
        self.assertEqual(report.installed, ['base-1', 'base-2', 'base-3'])
        for number in (1, 2, 3):
            header = (self.modules_root() / f'base-{number}' / 'module.toml').read_text(encoding='utf-8')
            self.assertIn(f'slug = "base-{number}"', header)
            self.assertIn('copies = 3', header)

    def test_a_single_copy_keeps_the_bare_slug(self) -> None:
        self.add_module('base', 'slug = "base"\nevents = ["session-start"]\n')
        self.assertEqual(self.run_installer().installed, ['base'])

    def test_the_hook_line_carries_a_timeout_long_enough_for_the_collection(self) -> None:
        # Без своего timeout харнес убивает хук через минуту, а стартовый
        # контекст ходит в Craft и в минуту не обязан укладываться.
        self.add_module('base', 'slug = "base"\nevents = ["session-start"]\n')
        self.run_installer()
        self.assertEqual(self.jarvis_handlers('SessionStart')[0]['timeout'],
                         installer.HOOK_TIMEOUT_SEC)
        self.assertGreaterEqual(installer.HOOK_TIMEOUT_SEC, 600)

    def test_one_line_per_copy(self) -> None:
        self.add_module('base', 'slug = "base"\nevents = ["session-start"]\ncopies = 4\n')
        self.run_installer()
        self.assertEqual(len(self.jarvis_handlers('SessionStart')), 4)

    def test_two_unified_events_of_one_harness_event_share_the_line(self) -> None:
        # Claude приносит старт сессии и «после сжатия» одним SessionStart:
        # строка одна, а единых имён в ней два — какое пришло, решает обёртка.
        self.add_module('base', 'slug = "base"\nevents = ["session-start", "after-compact"]\ncopies = 2\n')
        self.run_installer()
        handlers = self.jarvis_handlers('SessionStart')
        self.assertEqual(len(handlers), 2)
        self.assertEqual(handlers[0]['args'][1:],
                         ['--harness', 'claude', '--event', 'session-start',
                          '--event', 'after-compact'])

    def test_a_laid_in_event_gets_its_own_harness_line_without_copies(self) -> None:
        # Три заложенных заранее события идут общим правилом установщика:
        # строка на модуль и событие харнеса, ничего особенного. Копии тут не
        # нужны — копия нужна там, где параллельно собирают данные, а замок и
        # проба на событии по одной.
        self.add_module('lock', 'slug = "lock"\nevents = ["permission-request"]\n')
        self.run_installer()
        handlers = self.jarvis_handlers('PermissionRequest')
        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0]['args'][1:],
                         ['--harness', 'claude', '--event', 'permission-request'])

    def test_all_three_laid_in_events_get_their_claude_lines(self) -> None:
        self.add_module(
            'watcher',
            'slug = "watcher"\nevents = ["permission-request", "subagent-start", "model-message"]\n',
        )
        self.run_installer()
        for claude_event, unified in (('PermissionRequest', 'permission-request'),
                                      ('SubagentStart', 'subagent-start'),
                                      ('MessageDisplay', 'model-message')):
            handlers = self.jarvis_handlers(claude_event)
            self.assertEqual(len(handlers), 1, claude_event)
            self.assertEqual(handlers[0]['args'][1:],
                             ['--harness', 'claude', '--event', unified])

    def test_codex_warns_about_the_event_it_does_not_have(self) -> None:
        # «Ответа и мысли модели» у Codex нет — установщик говорит, в каком
        # харнесе модуль не сработает, и ставит его всё равно.
        self.add_module('watcher', 'slug = "watcher"\nevents = ["model-message"]\n')
        report = self.run_installer('--harness', 'codex', '--no-trust')
        self.assertIn('watcher', report.installed)
        self.assertTrue(
            any('model-message' in warning and 'codex' in warning for warning in report.warnings),
            report.warnings,
        )
        self.assertNotIn('MessageDisplay', self.config_toml())

    def test_codex_writes_lines_for_the_two_events_it_does_have(self) -> None:
        self.add_module(
            'watcher',
            'slug = "watcher"\nevents = ["permission-request", "subagent-start"]\n',
        )
        self.run_installer('--harness', 'codex', '--no-trust')
        config = self.config_toml()
        self.assertIn('[[hooks.PermissionRequest]]', config)
        self.assertIn('[[hooks.SubagentStart]]', config)

    def test_codex_lifts_the_cap_where_the_event_takes_context(self) -> None:
        # Ключ потолка пишется только там, где событие принимает текст в ход:
        # на старте подагента Codex его берёт, на запросе разрешения ругается.
        self.add_module('teller', 'slug = "teller"\nevents = ["subagent-start"]\n')
        self.add_module('lock', 'slug = "lock"\nevents = ["permission-request"]\n')
        self.run_installer('--harness', 'codex', '--no-trust')
        self.assertIn('additionalContextLimit = 0', self.codex_stanza('SubagentStart'))
        self.assertNotIn('additionalContextLimit', self.codex_stanza('PermissionRequest'))

    def test_fewer_copies_take_away_the_extra_folders_and_lines(self) -> None:
        self.add_module('base', 'slug = "base"\nevents = ["session-start"]\ncopies = 5\n')
        self.run_installer()
        (self.source / 'base' / 'module.toml').write_text(
            'slug = "base"\nevents = ["session-start"]\ncopies = 2\n', encoding='utf-8'
        )
        report = self.run_installer()
        self.assertEqual(len(self.jarvis_handlers('SessionStart')), 2)
        for number in (3, 4, 5):
            self.assertFalse((self.modules_root() / f'base-{number}').exists(), number)
        self.assertEqual(len(report.removed), 3)

    def test_a_copy_carries_its_own_core_and_parts(self) -> None:
        self.add_module('base', 'slug = "base"\nevents = ["session-start"]\ncopies = 2\n',
                        {'hooks': {'module.py': 'x = 1\n'}, 'rules': {'rule.md': '# правило\n'}})
        self.run_installer()
        for number in (1, 2):
            self.assertTrue((self.modules_root() / f'base-{number}' / '_core' / 'jarvis' / 'storage.py').is_file())
            self.assertTrue((self.modules_root() / f'base-{number}' / 'hooks' / 'module.py').is_file())
            self.assertTrue((self.settings_root / 'rules' / f'base-{number}' / 'rule.md').is_file())

    # --- Codex ---

    def test_codex_writes_a_hook_line_with_the_context_limit(self) -> None:
        self.add_module('base', 'slug = "base"\nevents = ["session-start"]\nharness = "codex"\n')
        self.run_installer('--harness', 'codex', '--no-trust')
        text = self.config_toml()
        self.assertIn('[[hooks.SessionStart]]', text)
        self.assertIn('[[hooks.SessionStart.hooks]]', text)
        self.assertIn('type = "command"', text)
        self.assertIn('additionalContextLimit = 0', text)
        self.assertIn(f'timeout = {installer.HOOK_TIMEOUT_SEC}', text)
        self.assertIn(str(self.modules_root() / 'base' / 'hooks' / 'module.py'), text)
        self.assertIn('--harness codex --event session-start', text)

    def test_codex_carries_both_our_events_on_one_line(self) -> None:
        # После сжатия Codex перезапускает тот же SessionStart с source=compact,
        # поэтому строка одна и называет оба наших имени, как у Claude.
        self.add_module('base',
                        'slug = "base"\nevents = ["session-start", "after-compact"]\nharness = "codex"\n')
        report = self.run_installer('--harness', 'codex', '--no-trust')
        text = self.config_toml()
        self.assertEqual(text.count('[[hooks.SessionStart.hooks]]'), 1)
        self.assertNotIn('PostCompact', text)
        self.assertIn('--harness codex --event session-start --event after-compact', text)
        self.assertIn('additionalContextLimit = 0', text)
        self.assertEqual(report.warnings, [])

    def test_codex_writes_one_line_per_copy(self) -> None:
        self.add_module('base',
                        'slug = "base"\nevents = ["session-start", "after-compact"]\n'
                        'harness = "codex"\ncopies = 3\n')
        self.run_installer('--harness', 'codex', '--no-trust')
        text = self.config_toml()
        self.assertEqual(text.count('[[hooks.SessionStart]]'), 3)
        self.assertEqual(text.count('[[hooks.SessionStart.hooks]]'), 3)
        self.assertNotIn('PostCompact', text)

    def test_codex_keeps_what_is_not_ours_in_the_config(self) -> None:
        self.settings_root.mkdir(parents=True)
        (self.settings_root / 'config.toml').write_text('model = "gpt"\n', encoding='utf-8')
        self.add_module('base', 'slug = "base"\nevents = ["session-start"]\nharness = "codex"\n')
        self.run_installer('--harness', 'codex', '--no-trust')
        self.run_installer('--harness', 'codex', '--no-trust')
        text = self.config_toml()
        self.assertIn('model = "gpt"', text)
        self.assertEqual(text.count('[[hooks.SessionStart.hooks]]'), 1)

    def test_codex_rules_land_between_the_markers_of_agents_md(self) -> None:
        self.add_module('code', 'slug = "code"\n', {'rules': {'code.md': '# Правила кода\n'}})
        self.run_installer('--harness', 'codex', '--no-trust')
        text = self.agents_md()
        self.assertIn('<!-- jarvis:code -->', text)
        self.assertIn('# Правила кода', text)
        self.assertIn('<!-- /jarvis:code -->', text)

    def test_codex_rules_do_not_pile_up_on_reinstall(self) -> None:
        self.add_module('code', 'slug = "code"\n', {'rules': {'code.md': '# Правила кода\n'}})
        self.run_installer('--harness', 'codex', '--no-trust')
        self.run_installer('--harness', 'codex', '--no-trust')
        self.assertEqual(self.agents_md().count('<!-- jarvis:code -->'), 1)

    def test_a_module_gone_from_the_source_leaves_no_section_in_agents_md(self) -> None:
        folder = self.add_module('code', 'slug = "code"\n', {'rules': {'code.md': '# Правила кода\n'}})
        self.run_installer('--harness', 'codex', '--no-trust')
        shutil.rmtree(folder)
        self.add_module('other', 'slug = "other"\n')
        self.run_installer('--harness', 'codex', '--no-trust')
        self.assertNotIn('jarvis:code', self.agents_md())

    def test_what_a_person_wrote_in_agents_md_survives(self) -> None:
        self.settings_root.mkdir(parents=True)
        (self.settings_root / 'AGENTS.md').write_text('# Моё\n\nне трогать\n', encoding='utf-8')
        self.add_module('code', 'slug = "code"\n', {'rules': {'code.md': '# Правила кода\n'}})
        self.run_installer('--harness', 'codex', '--no-trust')
        self.assertIn('не трогать', self.agents_md())

    def test_codex_asks_the_app_server_to_trust_exactly_our_lines(self) -> None:
        self.add_module('base', 'slug = "base"\nevents = ["session-start"]\nharness = "codex"\ncopies = 2\n')
        report = self.run_installer(
            '--harness', 'codex', '--codex-cli', str(self.fake_cli),
        )
        written = json.loads(self.journal.read_text(encoding='utf-8'))
        keys = [entry['keyPath'] for entry in written]
        self.assertEqual(len(keys), 4, keys)  # две строки × (trusted_hash + enabled)
        for number in (0, 1):
            config = self.settings_root / 'config.toml'
            self.assertIn(f'hooks.state."{config}:session_start:{number}:0".trusted_hash', keys)
            self.assertIn(f'hooks.state."{config}:session_start:{number}:0".enabled', keys)
        self.assertIn('sha256:hash-0', [entry['value'] for entry in written])
        self.assertEqual(report.warnings, [])

    def test_a_missing_codex_is_a_warning_and_not_a_crash(self) -> None:
        self.add_module('base', 'slug = "base"\nevents = ["session-start"]\nharness = "codex"\n')
        report = self.run_installer('--harness', 'codex', '--codex-cli', str(self.root / 'no-such-codex'))
        self.assertTrue(any('доверие' in warning for warning in report.warnings), report.warnings)
        self.assertIn('[[hooks.SessionStart]]', self.config_toml())


if __name__ == '__main__':
    unittest.main()


class ForeignHooksTest(unittest.TestCase):
    """Чужое в настройках харнеса установщик не трогает."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings_root = self.root / 'settings'
        self.source = self.root / 'source'
        self.source.mkdir()

    def run_installer(self, *extra: str):
        return installer.install(installer.parse_args([
            '--settings-dir', str(self.settings_root),
            '--modules', str(self.source),
            '--core', str(CORE_SOURCE),
            '--python', '/usr/bin/python3',
            *extra,
        ]))

    def add_module(self, slug: str, header: str) -> None:
        folder = self.source / slug
        folder.mkdir()
        (folder / 'module.toml').write_text(header, encoding='utf-8')

    def test_a_foreign_module_py_hook_is_not_ours_and_survives(self) -> None:
        # Чужой хук может называться module.py и брать --event: признак нашего —
        # путь в наш каталог модулей, а не имя файла.
        foreign = {'type': 'command', 'command': '/usr/bin/python3',
                   'args': ['/home/vlad/scripts/module.py', '--event', 'custom', '--verbose']}
        self.settings_root.mkdir(parents=True)
        (self.settings_root / 'settings.json').write_text(
            json.dumps({'hooks': {'UserPromptSubmit': [{'hooks': [foreign]}]}}), encoding='utf-8'
        )
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        self.run_installer()
        settings = json.loads((self.settings_root / 'settings.json').read_text(encoding='utf-8'))
        handlers = [h for g in settings['hooks']['UserPromptSubmit'] for h in g['hooks']]
        self.assertIn(foreign, handlers)
        self.assertEqual(len(handlers), 2)  # чужой плюс наш, и ни одного лишнего

    def test_our_own_line_is_recognised_as_ours(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n')
        self.run_installer()
        settings = json.loads((self.settings_root / 'settings.json').read_text(encoding='utf-8'))
        ours = [h for g in settings['hooks']['UserPromptSubmit'] for h in g['hooks']]
        modules_dir = installer.modules_root(self.settings_root)
        self.assertTrue(all(installer.is_jarvis_line(h, modules_dir) for h in ours))

    def test_a_copy_slug_that_collides_with_a_module_stops_the_install(self) -> None:
        # `base` с copies = 2 порождает `base-1`: модуль с таким slug'ом занял бы
        # ту же папку и ту же запись журнала, и половина набора пропала бы молча.
        self.add_module('base', 'slug = "base"\nevents = ["prompt"]\ncopies = 2\n')
        self.add_module('base-1', 'slug = "base-1"\nevents = ["prompt"]\n')
        with self.assertRaises(ValueError) as caught:
            self.run_installer()
        self.assertIn('base-1', str(caught.exception))
        self.assertFalse(self.settings_root.exists())

    def test_copies_that_do_not_collide_install(self) -> None:
        self.add_module('base', 'slug = "base"\nevents = ["prompt"]\ncopies = 2\n')
        self.add_module('other', 'slug = "other"\nevents = ["prompt"]\n')
        self.assertEqual(sorted(self.run_installer().installed), ['base-1', 'base-2', 'other'])
