"""Часть skill на обоих харнесах: соседние файлы и таблица возможностей.

Раскладка части skill проверяется отдельно от остальных частей, потому что у
неё есть две вещи, которых нет ни у кого: список соседних файлов модуля и
подстановка единых slug возможностей харнеса.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from jarvis import manifest as manifest_reader

from . import CORE_SOURCE, MODULES_DIR
from .test_installer import installer


class SkillInstallCase(unittest.TestCase):
    """Общая подготовка: пустой каталог настроек и набор модулей из временной папки."""

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
            part_dir.mkdir(parents=True, exist_ok=True)
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

    def run_codex(self):
        return self.run_installer('--harness', 'codex', '--no-trust')

    def skill_dir(self, slug: str) -> Path:
        return self.settings_root / 'skills' / slug

    def agents_md(self) -> str:
        return (self.settings_root / 'AGENTS.md').read_text(encoding='utf-8')


class SkillPartTest(SkillInstallCase):
    """Раскладка части skill и список соседних файлов модуля."""

    def test_claude_reads_skills_from_its_settings_dir(self) -> None:
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': '# скилл\n'}})
        self.run_installer()
        self.assertTrue((self.skill_dir('probe') / 'SKILL.md').is_file())

    def test_codex_reads_skills_from_its_home(self) -> None:
        # Замер 18.09.2026, строки codex-cli 0.155.0: «Installs into
        # $CODEX_HOME/skills/<skill-name> (defaults to ~/.codex/skills)».
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': '# скилл\n'}})
        self.run_codex()
        self.assertTrue((self.skill_dir('probe') / 'SKILL.md').is_file())

    def test_neighbouring_files_of_the_part_travel_with_skill_md(self) -> None:
        self.add_module('probe', 'slug = "probe"\n',
                        {'skill': {'SKILL.md': '# скилл\n', 'reference.md': '# справка\n'}})
        self.run_installer()
        self.assertTrue((self.skill_dir('probe') / 'reference.md').is_file())

    # --- список соседних файлов модуля ---

    def test_a_declared_file_lands_beside_skill_md(self) -> None:
        self.add_module('craft-call', 'slug = "craft-call"\n',
                        {'rules': {'craft-tool.md': '# механика\n'},
                         'skill': {'SKILL.md': '# скилл\n', 'files': 'rules/craft-tool.md\n'}})
        self.run_installer()
        self.assertTrue((self.skill_dir('craft-call') / 'craft-tool.md').is_file())

    def test_a_declared_file_has_one_source(self) -> None:
        # Правка руками в двух местах исключена: копию кладёт установщик, и она
        # совпадает с единственным источником побайтно.
        text = '# механика\n\nдлинный текст правил\n'
        self.add_module('craft-call', 'slug = "craft-call"\n',
                        {'rules': {'craft-tool.md': text},
                         'skill': {'SKILL.md': '# скилл\n', 'files': 'rules/craft-tool.md\n'}})
        self.run_installer()
        beside = self.skill_dir('craft-call') / 'craft-tool.md'
        self.assertEqual(beside.read_bytes(), (self.source / 'craft-call' / 'rules' / 'craft-tool.md').read_bytes())

    def test_the_list_itself_is_not_installed(self) -> None:
        self.add_module('craft-call', 'slug = "craft-call"\n',
                        {'rules': {'craft-tool.md': '# механика\n'},
                         'skill': {'SKILL.md': '# скилл\n', 'files': 'rules/craft-tool.md\n'}})
        self.run_installer()
        self.assertFalse((self.skill_dir('craft-call') / 'files').exists())

    def test_comments_and_blank_lines_in_the_list_are_skipped(self) -> None:
        self.add_module('probe', 'slug = "probe"\n',
                        {'rules': {'rule.md': '# правило\n'},
                         'skill': {'SKILL.md': '#\n', 'files': '# зачем\n\nrules/rule.md\n\n'}})
        self.run_installer()
        self.assertTrue((self.skill_dir('probe') / 'rule.md').is_file())

    def test_a_declared_file_that_does_not_exist_stops_the_install(self) -> None:
        self.add_module('probe', 'slug = "probe"\n',
                        {'skill': {'SKILL.md': '#\n', 'files': 'rules/missing.md\n'}})
        with self.assertRaises(ValueError) as caught:
            self.run_installer()
        self.assertIn('rules/missing.md', str(caught.exception))

    def test_a_declared_path_outside_the_module_stops_the_install(self) -> None:
        self.add_module('probe', 'slug = "probe"\n',
                        {'skill': {'SKILL.md': '#\n', 'files': '../other/secret.md\n'}})
        self.add_module('other', 'slug = "other"\n', {'skill': {'SKILL.md': '#\n'}})
        (self.source / 'other' / 'secret.md').write_text('чужое\n', encoding='utf-8')
        with self.assertRaises(ValueError) as caught:
            self.run_installer()
        self.assertIn('за папку модуля', str(caught.exception))

    def test_two_declared_files_with_one_name_stop_the_install(self) -> None:
        self.add_module('probe', 'slug = "probe"\n',
                        {'rules': {'rule.md': 'один\n'},
                         'data': {'rule.md': 'другой\n'},
                         'skill': {'SKILL.md': '#\n', 'files': 'rules/rule.md\ndata/rule.md\n'}})
        with self.assertRaises(ValueError) as caught:
            self.run_installer()
        self.assertIn('rule.md', str(caught.exception))

    def test_a_declared_file_may_not_overwrite_a_file_of_the_part(self) -> None:
        self.add_module('probe', 'slug = "probe"\n',
                        {'rules': {'note.md': 'из правил\n'},
                         'skill': {'SKILL.md': '#\n', 'note.md': 'своё\n', 'files': 'rules/note.md\n'}})
        with self.assertRaises(ValueError) as caught:
            self.run_installer()
        self.assertIn('note.md', str(caught.exception))

    def test_reinstall_with_declared_files_changes_nothing(self) -> None:
        self.add_module('craft-call', 'slug = "craft-call"\n',
                        {'rules': {'craft-tool.md': '# механика\n'},
                         'skill': {'SKILL.md': '# скилл\n', 'files': 'rules/craft-tool.md\n'}})
        self.run_installer()
        before = {p.relative_to(self.settings_root): p.read_bytes()
                  for p in sorted(self.skill_dir('craft-call').rglob('*')) if p.is_file()}
        report = self.run_installer()
        after = {p.relative_to(self.settings_root): p.read_bytes()
                 for p in sorted(self.skill_dir('craft-call').rglob('*')) if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(report.removed, [])

    def test_a_declared_file_is_dropped_when_the_list_drops_it(self) -> None:
        folder = self.add_module('craft-call', 'slug = "craft-call"\n',
                                 {'rules': {'craft-tool.md': '# механика\n'},
                                  'skill': {'SKILL.md': '#\n', 'files': 'rules/craft-tool.md\n'}})
        self.run_installer()
        (folder / 'skill' / 'files').write_text('', encoding='utf-8')
        self.run_installer()
        self.assertFalse((self.skill_dir('craft-call') / 'craft-tool.md').exists())

    def test_only_the_skill_part_reads_the_list(self) -> None:
        # У части agents файл `files` — обычный файл, а не объявление.
        self.add_module('probe', 'slug = "probe"\n',
                        {'rules': {'rule.md': '# правило\n'},
                         'agents': {'reviewer.md': '#\n', 'files': 'rules/rule.md\n'}})
        self.run_installer()
        agents = self.settings_root / 'agents' / 'probe'
        self.assertTrue((agents / 'files').is_file())
        self.assertFalse((agents / 'rule.md').exists())


class HarnessSlugTest(SkillInstallCase):
    """Единый slug возможности в текстовых частях: форма своя у каждого харнеса."""

    def test_ask_becomes_the_claude_form_in_a_skill(self) -> None:
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': 'Спросить {{ASK}}.\n'}})
        self.run_installer()
        text = (self.skill_dir('probe') / 'SKILL.md').read_text(encoding='utf-8')
        self.assertEqual(text, 'Спросить инструментом AskUserQuestion.\n')

    def test_ask_becomes_the_codex_form_in_a_skill(self) -> None:
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': 'Спросить {{ASK}}.\n'}})
        self.run_codex()
        text = (self.skill_dir('probe') / 'SKILL.md').read_text(encoding='utf-8')
        self.assertEqual(text, 'Спросить текстом в ответе, варианты строками.\n')

    def test_ask_becomes_the_claude_form_in_rules(self) -> None:
        self.add_module('behavior', 'slug = "behavior"\n',
                        {'rules': {'behavior.md': 'Вопрос задаётся {{ASK}}.\n'}})
        self.run_installer()
        text = (self.settings_root / 'rules' / 'behavior' / 'behavior.md').read_text(encoding='utf-8')
        self.assertEqual(text, 'Вопрос задаётся инструментом AskUserQuestion.\n')

    def test_ask_becomes_the_codex_form_in_agents_md(self) -> None:
        self.add_module('behavior', 'slug = "behavior"\n',
                        {'rules': {'behavior.md': 'Вопрос задаётся {{ASK}}.\n'}})
        self.run_codex()
        self.assertIn('Вопрос задаётся текстом в ответе, варианты строками.', self.agents_md())
        self.assertNotIn('{{ASK}}', self.agents_md())

    def test_ask_is_substituted_in_a_declared_file_too(self) -> None:
        self.add_module('craft-call', 'slug = "craft-call"\n',
                        {'rules': {'craft-tool.md': 'Спросить {{ASK}}.\n'},
                         'skill': {'SKILL.md': '#\n', 'files': 'rules/craft-tool.md\n'}})
        self.run_installer()
        beside = (self.skill_dir('craft-call') / 'craft-tool.md').read_text(encoding='utf-8')
        self.assertEqual(beside, 'Спросить инструментом AskUserQuestion.\n')

    def test_an_unknown_slug_in_a_text_part_stops_the_install(self) -> None:
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': 'Позвать {{SHOUT}}.\n'}})
        with self.assertRaises(ValueError) as caught:
            self.run_installer()
        message = str(caught.exception)
        self.assertIn('probe', message)
        self.assertIn('{{SHOUT}}', message)

    def test_an_unknown_slug_leaves_the_previous_part_untouched(self) -> None:
        # Проверка идёт до первой стирающей операции: упавшая установка не
        # имеет права оставить харнесу полусобранный скилл вместо рабочего.
        folder = self.add_module('probe', 'slug = "probe"\n',
                                 {'skill': {'SKILL.md': '# рабочий\n', 'note.md': 'цел\n'}})
        self.run_installer()
        (folder / 'skill' / 'SKILL.md').write_text('# новый\n', encoding='utf-8')
        (folder / 'skill' / 'note.md').write_text('позвать {{SHOUT}}\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.run_installer()
        installed = self.skill_dir('probe')
        self.assertEqual((installed / 'SKILL.md').read_text(encoding='utf-8'), '# рабочий\n')
        self.assertEqual((installed / 'note.md').read_text(encoding='utf-8'), 'цел\n')

    def test_an_unknown_slug_leaves_no_directory_on_a_first_install(self) -> None:
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': 'Позвать {{SHOUT}}.\n'}})
        with self.assertRaises(ValueError):
            self.run_installer()
        self.assertFalse(self.skill_dir('probe').exists())
        self.assertFalse(self.settings_root.exists())

    def test_a_broken_list_of_files_stops_the_install_before_it_touches_anything(self) -> None:
        folder = self.add_module('probe', 'slug = "probe"\n',
                                 {'rules': {'rule.md': '# правило\n'},
                                  'skill': {'SKILL.md': '# рабочий\n', 'files': 'rules/rule.md\n'}})
        self.run_installer()
        (folder / 'skill' / 'files').write_text('rules/missing.md\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.run_installer()
        self.assertEqual((self.skill_dir('probe') / 'SKILL.md').read_text(encoding='utf-8'), '# рабочий\n')
        self.assertTrue((self.skill_dir('probe') / 'rule.md').is_file())

    def test_a_dry_run_catches_an_unknown_slug(self) -> None:
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': 'Позвать {{SHOUT}}.\n'}})
        with self.assertRaises(ValueError):
            self.run_installer('--dry-run')

    def test_an_unknown_slug_in_rules_stops_the_install_on_both_harnesses(self) -> None:
        self.add_module('probe', 'slug = "probe"\n', {'rules': {'rule.md': 'Позвать {{SHOUT}}.\n'}})
        for extra in ((), ('--harness', 'codex', '--no-trust')):
            with self.assertRaises(ValueError) as caught:
                self.run_installer(*extra)
            self.assertIn('{{SHOUT}}', str(caught.exception))

    def test_an_unknown_slug_in_agents_stops_the_install(self) -> None:
        self.add_module('probe', 'slug = "probe"\n', {'agents': {'reviewer.md': '{{SHOUT}}\n'}})
        with self.assertRaises(ValueError) as caught:
            self.run_installer()
        self.assertIn('{{SHOUT}}', str(caught.exception))

    def test_code_of_a_module_is_left_alone(self) -> None:
        # В коде имена харнеса переводит обёртка, поэтому фигурные скобки в
        # хуках и данных — не наше дело: шаблон чужого формата остаётся как был.
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n',
                        {'hooks': {'module.py': 'TEMPLATE = "{{NAME}}"\n'},
                         'data': {'case.json': '{"text": "{{NAME}}"}\n'}})
        self.run_installer()
        modules = installer.modules_root(self.settings_root) / 'probe'
        self.assertIn('{{NAME}}', (modules / 'hooks' / 'module.py').read_text(encoding='utf-8'))
        self.assertIn('{{NAME}}', (modules / 'data' / 'case.json').read_text(encoding='utf-8'))


class SkillsReadPermissionTest(SkillInstallCase):
    """Право на чтение каталога скиллов: Claude без него скилл читать не даёт."""

    def settings(self) -> dict:
        return json.loads((self.settings_root / 'settings.json').read_text(encoding='utf-8'))

    def rule(self) -> str:
        return f'Read(/{self.settings_root}/skills/**)'

    def test_a_skill_part_brings_the_right_to_read_it(self) -> None:
        # Форма `//путь` — абсолютный путь от корня файловой системы (дока прав
        # Claude Code, 18.09.2026): одинарный слэш анкерит на источник настроек.
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': '#\n'}})
        self.run_installer()
        self.assertEqual(self.settings()['permissions']['allow'], [self.rule()])
        self.assertTrue(self.rule().startswith('Read(//'))

    def test_without_a_skill_part_nothing_is_written(self) -> None:
        self.add_module('probe', 'slug = "probe"\nevents = ["prompt"]\n', {'rules': {'rule.md': '#\n'}})
        self.run_installer()
        self.assertNotIn('permissions', self.settings())

    def test_the_right_does_not_pile_up_on_reinstall(self) -> None:
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': '#\n'}})
        self.run_installer()
        before = (self.settings_root / 'settings.json').read_text(encoding='utf-8')
        self.run_installer()
        self.assertEqual((self.settings_root / 'settings.json').read_text(encoding='utf-8'), before)
        self.assertEqual(self.settings()['permissions']['allow'], [self.rule()])

    def test_the_last_skill_part_gone_takes_the_right_with_it(self) -> None:
        folder = self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': '#\n'}})
        self.run_installer()
        shutil.rmtree(folder / 'skill')
        (folder / 'rules').mkdir()
        (folder / 'rules' / 'rule.md').write_text('# правило\n', encoding='utf-8')
        self.run_installer()
        self.assertNotIn('permissions', self.settings())

    def test_one_skill_left_keeps_the_right(self) -> None:
        folder = self.add_module('one', 'slug = "one"\n', {'skill': {'SKILL.md': '#\n'}})
        self.add_module('two', 'slug = "two"\n', {'skill': {'SKILL.md': '#\n'}})
        self.run_installer()
        shutil.rmtree(folder / 'skill')
        self.run_installer()
        self.assertEqual(self.settings()['permissions']['allow'], [self.rule()])

    def test_what_a_person_wrote_in_permissions_survives(self) -> None:
        self.settings_root.mkdir(parents=True)
        (self.settings_root / 'settings.json').write_text(
            json.dumps({'permissions': {'allow': ['Bash(ls:*)'], 'deny': ['Read(./.env)'],
                                        'defaultMode': 'acceptEdits'}}),
            encoding='utf-8',
        )
        folder = self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': '#\n'}})
        self.run_installer()
        permissions = self.settings()['permissions']
        self.assertEqual(permissions['allow'], ['Bash(ls:*)', self.rule()])
        self.assertEqual(permissions['deny'], ['Read(./.env)'])
        self.assertEqual(permissions['defaultMode'], 'acceptEdits')

        shutil.rmtree(folder / 'skill')
        (folder / 'rules').mkdir()
        (folder / 'rules' / 'rule.md').write_text('#\n', encoding='utf-8')
        self.run_installer()
        permissions = self.settings()['permissions']
        self.assertEqual(permissions['allow'], ['Bash(ls:*)'])
        self.assertEqual(permissions['deny'], ['Read(./.env)'])

    def test_codex_needs_no_such_right(self) -> None:
        # Живой `codex exec` читает соседний файл скилла без разрешений
        # (замер 18.09.2026), поэтому у Codex ничего не пишется.
        self.add_module('probe', 'slug = "probe"\n', {'skill': {'SKILL.md': '#\n'}})
        self.run_codex()
        self.assertFalse((self.settings_root / 'settings.json').exists())
        self.assertNotIn('permissions', (self.settings_root / 'config.toml').read_text(encoding='utf-8'))


class RepositoryModulesTest(unittest.TestCase):
    """Набор самого репозитория: один источник у механики Craft."""

    def test_the_new_skill_modules_declare_what_they_lean_on(self) -> None:
        # Тело скилла ссылается на файлы соседних модулей: отданный отдельно,
        # без requires он встал бы молча и сослался на то, чего нет.
        expected = {
            'craft-groceries': {'craft-call'},
            'craft-inbox': {'craft-call', 'behavior'},
            'craft-project-review': {'craft-call', 'behavior'},
            'craft-incident': {'craft-call', 'behavior', 'code'},
        }
        for slug, needed in expected.items():
            manifest = manifest_reader.load(MODULES_DIR / slug)
            self.assertEqual(set(manifest.requires), needed, slug)

    def test_craft_call_names_the_installed_copy_by_an_absolute_path(self) -> None:
        # В репозитории craft лежит файл с тем же именем: по относительному
        # пути модель читает его, а не установленную копию.
        source = (MODULES_DIR / 'craft-call' / 'skill' / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('{{HARNESS_SETTINGS}}/skills/craft-call/craft-tool.md', source)

    def test_craft_call_keeps_the_mechanics_in_one_file(self) -> None:
        skill = MODULES_DIR / 'craft-call' / 'skill'
        listed = [line.strip() for line in (skill / 'files').read_text(encoding='utf-8').splitlines()
                  if line.strip() and not line.startswith('#')]
        self.assertEqual(listed, ['rules/craft-tool.md'])
        self.assertFalse((skill / 'craft-tool.md').exists(),
                         'копия механики внутри части skill — это вторая правка руками')

    def test_every_module_of_the_repository_installs(self) -> None:
        # Неизвестный slug и битый список соседних файлов ловятся здесь, на
        # настоящем наборе, а не в живой сессии.
        for harness in ('claude', 'codex'):
            with tempfile.TemporaryDirectory() as tmp:
                argv = [
                    '--harness', harness,
                    '--settings-dir', str(Path(tmp) / 'settings'),
                    '--modules', str(MODULES_DIR),
                    '--core', str(CORE_SOURCE),
                    '--state-dir', str(Path(tmp) / 'state'),
                    '--python', '/usr/bin/python3',
                ]
                if harness == 'codex':
                    argv.append('--no-trust')
                report = installer.install(installer.parse_args(argv))
                self.assertIn('craft-call', report.installed)
                skill_dir = Path(tmp) / 'settings' / 'skills' / 'craft-call'
                beside = skill_dir / 'craft-tool.md'
                # Rules travel beside the skill from their one source; their
                # harness-path placeholders are resolved during installation.
                expected = installer.substitute(
                    (MODULES_DIR / 'craft-call' / 'rules' / 'craft-tool.md').read_text(encoding='utf-8'),
                    installer.placeholders(Path(tmp) / 'settings',
                                           Path(tmp) / 'settings' / 'jarvis' / 'modules' / 'craft-call',
                                           Path(tmp) / 'state',
                                           (installer.CodexBackend() if harness == 'codex'
                                            else installer.ClaudeBackend()).project_dir),
                )
                self.assertEqual(beside.read_text(encoding='utf-8'), expected)
                installed = (skill_dir / 'SKILL.md').read_text(encoding='utf-8')
                self.assertIn(str(beside), installed)
                self.assertNotIn('{{', installed)
                # Требования новых модулей должны сходиться с самим набором.
                self.assertEqual([w for w in report.warnings if 'нужен' in w], [], harness)


if __name__ == '__main__':
    unittest.main()
