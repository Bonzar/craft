"""Упоминания модулей: файл модуля называет только тех, кто у него в requires или for.

Решение Влада от 02.11.2026: упоминание другого модуля в любом файле модуля
обязано стоять в его `requires` или `for`, установщик это проверяет и падает,
назвав модуль, файл и строку.
"""

import tempfile
import unittest
from pathlib import Path

from . import CORE_SOURCE, MODULES_DIR
from .test_installer import installer


class MentionCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()

    def add_module(self, slug: str, header: str = '', files: dict[str, str] | None = None) -> None:
        folder = self.source / slug
        folder.mkdir()
        (folder / 'module.toml').write_text(f'slug = "{slug}"\n{header}', encoding='utf-8')
        for name, text in (files or {}).items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')

    def run_installer(self, modules: Path | None = None):
        argv = [
            '--settings-dir', str(self.root / 'settings'),
            '--modules', str(modules or self.source),
            '--core', str(CORE_SOURCE),
            '--state-dir', str(self.root / 'state'),
            '--python', '/usr/bin/python3',
            '--dry-run',
        ]
        return installer.install(installer.parse_args(argv))

    def assert_fails(self, *expected: str) -> None:
        with self.assertRaises(ValueError) as caught:
            self.run_installer()
        for text in expected:
            self.assertIn(text, str(caught.exception))

    def test_a_mention_outside_requires_fails_naming_module_file_and_line(self) -> None:
        self.add_module('craft-call')
        self.add_module('craft-note', files={'skill/SKILL.md': 'первая строка\nМеханика — craft-call.\n'})
        self.assert_fails('модуль «craft-note», файл skill/SKILL.md:2 называет модуль «craft-call»')

    def test_a_required_module_may_be_named(self) -> None:
        self.add_module('craft-call')
        self.add_module('craft-note', 'requires = ["craft-call"]\n', {'README.md': 'Механика — craft-call.\n'})
        self.assertEqual(sorted(self.run_installer().installed), ['craft-call', 'craft-note'])

    def test_a_comment_in_the_header_is_checked_too(self) -> None:
        self.add_module('craft-call')
        self.add_module('craft-note', '# его зовёт craft-call\n')
        self.assert_fails('файл module.toml:2 называет модуль «craft-call»')

    def test_an_adapter_may_name_its_host(self) -> None:
        self.add_module('turn-review')
        self.add_module('craft-note', 'for = ["turn-review"]\n', {'data/review.md': 'Критерий для turn-review.\n'})
        self.run_installer()

    def test_a_host_may_name_its_adapter(self) -> None:
        self.add_module('turn-review', files={'README.md': 'Поставщик — `craft-note`.\n'})
        self.add_module('craft-note', 'for = ["turn-review"]\n')
        self.run_installer()

    def test_a_host_may_not_name_a_module_that_only_requires_it(self) -> None:
        # Зависимый по requires — не адаптер: договора, по которому хозяин его
        # знает, нет.
        self.add_module('craft-sync', files={'README.md': 'Его зовёт craft-note.\n'})
        self.add_module('craft-note', 'requires = ["craft-sync"]\n')
        self.assert_fails('модуль «craft-sync», файл README.md:1 называет модуль «craft-note»')

    def test_a_family_mask_allows_every_member(self) -> None:
        self.add_module('start-context-claude')
        self.add_module('probe-data', 'for = ["start-context-*"]\n', {'README.md': 'Читает start-context-claude.\n'})
        self.run_installer()

    def test_self_mention_is_allowed(self) -> None:
        self.add_module('craft-note', files={'skill/SKILL.md': 'name: craft-note\n'})
        self.run_installer()

    def test_hyphen_boundary_keeps_slugs_apart(self) -> None:
        # `craft-decisions` — не `craft-decision`, `craft-decision-1` — копия,
        # а не сам модуль, `x-craft-decision` — чужое имя.
        self.add_module('craft-decision')
        self.add_module('craft-note', files={'README.md': 'craft-decisions, craft-decision-1, x-craft-decision\n'})
        self.run_installer()

    def test_a_slug_inside_a_path_counts(self) -> None:
        self.add_module('craft-call')
        self.add_module('craft-note', files={'README.md': 'см. modules/craft-call/rules/craft-tool.md\n'})
        self.assert_fails('называет модуль «craft-call»')

    def test_a_single_word_slug_as_an_english_word_does_not_count(self) -> None:
        self.add_module('probe')
        self.add_module('code')
        self.add_module('craft-note', files={
            'hooks/api.py': 'def probe(self):\n    return {"code": 1}  # probe the `code`\n',
        })
        self.run_installer()

    def test_a_single_word_slug_after_the_word_module_counts(self) -> None:
        self.add_module('behavior')
        self.add_module('code')
        self.add_module('craft-note', files={'skill/SKILL.md': 'Правила — в модулях behavior, code и craft-note.\n'})
        self.assert_fails('называет модуль «behavior»', 'называет модуль «code»')

    def test_a_single_word_slug_as_a_path_to_a_part_counts(self) -> None:
        self.add_module('probe')
        self.add_module('craft-note', files={'README.md': 'Маркер — `probe/skill/SKILL.md`.\n'})
        self.assert_fails('файл README.md:1 называет модуль «probe»')

    def test_a_name_outside_the_source_set_is_not_a_module(self) -> None:
        self.add_module('craft-note', files={'README.md': 'Сверяет craft-reconcile.\n'})
        self.run_installer()

    def test_an_exemption_covers_only_its_file_and_slugs(self) -> None:
        intro = "INTRO = 'craft-task для задачи, скилл craft-reconcile'\n"
        self.add_module('craft-task')
        self.add_module('craft-reconcile')
        self.add_module('craft-snapshot', files={'lib/__init__.py': intro, 'skill/SKILL.md': 'craft-task\n'})
        with self.assertRaises(ValueError) as caught:
            self.run_installer()
        self.assertIn('файл skill/SKILL.md:1 называет модуль «craft-task»', str(caught.exception))
        self.assertNotIn('lib/__init__.py', str(caught.exception))

    def test_the_real_set_installs(self) -> None:
        self.run_installer(MODULES_DIR)


if __name__ == '__main__':
    unittest.main()
