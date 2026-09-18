"""Разбор шапки, поиск по requires и поиск адаптеров базы."""

import tempfile
import unittest
from pathlib import Path

from jarvis import manifest as m


class ParseTest(unittest.TestCase):
    def test_six_field_header_is_read(self) -> None:
        parsed = m.parse(
            'slug = "lock-irreversible-shell"\n'
            'for = ["shell-tree-*"]\n'
            'events = ["pre-tool", "prompt"]\n'
            'requires = ["shell-tree"]\n'
            'harness = "claude"\n'
            'copies = 3\n'
        )
        self.assertEqual(parsed.slug, 'lock-irreversible-shell')
        self.assertEqual(parsed.serves, ('shell-tree-*',))
        self.assertEqual(parsed.events, ('pre-tool', 'prompt'))
        self.assertEqual(parsed.requires, ('shell-tree',))
        self.assertEqual(parsed.harness, 'claude')
        self.assertEqual(parsed.copies, 3)

    def test_header_without_optional_fields_is_valid(self) -> None:
        parsed = m.parse('slug = "probe"\nevents = ["prompt"]\nrequires = []\n')
        self.assertEqual(parsed.serves, ())
        self.assertEqual(parsed.requires, ())
        self.assertIsNone(parsed.harness)
        self.assertEqual(parsed.copies, 1)

    def test_for_accepts_a_bare_string_as_a_list_of_one(self) -> None:
        self.assertEqual(m.parse('slug = "a"\nfor = "start-context-*"\n').serves, ('start-context-*',))

    def test_for_lists_every_thing_the_adapter_serves(self) -> None:
        parsed = m.parse('slug = "a"\nfor = ["start-context-*", "digest"]\n')
        self.assertEqual(parsed.serves, ('start-context-*', 'digest'))

    def test_for_must_hold_strings(self) -> None:
        with self.assertRaises(ValueError):
            m.parse('slug = "a"\nfor = [1]\n')

    def test_harness_is_an_optional_string(self) -> None:
        self.assertEqual(m.parse('slug = "a"\nharness = "codex"\n').harness, 'codex')
        with self.assertRaises(ValueError):
            m.parse('slug = "a"\nharness = ""\n')

    def test_copies_must_be_a_whole_number_of_at_least_one(self) -> None:
        for bad in ('0', '-2', '"3"', 'true', '1.5'):
            with self.assertRaises(ValueError, msg=bad):
                m.parse(f'slug = "a"\nevents = ["prompt"]\ncopies = {bad}\n')

    def test_copies_belongs_to_hooks_only(self) -> None:
        # Копия — это копия строки хука: без events копировать нечего.
        with self.assertRaises(ValueError) as caught:
            m.parse('slug = "a"\ncopies = 4\n')
        self.assertIn('copies', str(caught.exception))

    def test_copy_slug_carries_the_number_and_gives_it_back(self) -> None:
        self.assertEqual(m.copy_slug('start-context-claude', 7), 'start-context-claude-7')
        self.assertEqual(m.copy_index('start-context-claude-7'), 7)
        self.assertEqual(m.copy_index('start-context-claude'), 1)

    def test_missing_slug_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            m.parse('events = ["prompt"]\n')

    def test_unknown_field_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as caught:
            m.parse('slug = "probe"\nkind = "hook"\n')
        self.assertIn('kind', str(caught.exception))

    def test_event_outside_the_catalog_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as caught:
            m.parse('slug = "probe"\nevents = ["перед вызовом"]\n')
        self.assertIn('едином каталоге', str(caught.exception))

    def test_requires_must_be_a_list_of_strings(self) -> None:
        with self.assertRaises(ValueError):
            m.parse('slug = "probe"\nrequires = "shell-tree"\n')

    def test_empty_for_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            m.parse('slug = "probe"\nfor = ""\n')
        with self.assertRaises(ValueError):
            m.parse('slug = "probe"\nfor = [" "]\n')

    def test_slug_is_limited_to_lowercase_letters_digits_and_hyphen(self) -> None:
        for slug in ('probe', 'shell-tree', 'probe2', 'a-1'):
            self.assertEqual(m.parse(f'slug = "{slug}"').slug, slug)

    def test_slug_that_is_not_a_safe_path_segment_is_rejected(self) -> None:
        for slug in ('../../outside', '/etc/jarvis', 'a/b', 'a b', 'ядро', 'under_score', '.'):
            with self.assertRaises(ValueError, msg=slug):
                m.parse(f'slug = "{slug}"')

    def test_an_uppercase_slug_is_rejected(self) -> None:
        # Slug — имя папки, а на macOS файловая система регистронезависима:
        # «Probe» и «probe» столкнулись бы.
        for slug in ('Probe', 'A-1', 'shellTree'):
            with self.assertRaises(ValueError, msg=slug):
                m.parse(f'slug = "{slug}"')

    def test_core_folder_name_can_never_be_a_slug(self) -> None:
        # Поэтому списка запрещённых имён и не нужно: `_core` не проходит разбор.
        with self.assertRaises(ValueError):
            m.parse('slug = "_core"')

    def test_load_reads_the_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'probe'
            folder.mkdir()
            (folder / m.MANIFEST_NAME).write_text('slug = "probe"\n', encoding='utf-8')
            self.assertEqual(m.load(folder).slug, 'probe')

    def test_slug_does_not_depend_on_the_folder_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'any-folder-name'
            folder.mkdir()
            (folder / m.MANIFEST_NAME).write_text('slug = "real-slug"\n', encoding='utf-8')
            self.assertEqual(m.load(folder).slug, 'real-slug')


class RequiresTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plain = m.Manifest(slug='shell-tree')
        self.family_member = m.Manifest(slug='shell-tree-bash')
        self.adapter = m.Manifest(slug='bash-adapter', serves=('shell-tree-*',))
        self.stranger = m.Manifest(slug='other')

    def test_plain_slug_matches_by_slug(self) -> None:
        self.assertTrue(m.matches('shell-tree', self.plain))
        self.assertFalse(m.matches('shell-tree', self.stranger))

    def test_plain_slug_does_not_match_a_family_member(self) -> None:
        self.assertFalse(m.matches('shell-tree', self.family_member))

    def test_family_mask_matches_by_slug(self) -> None:
        self.assertTrue(m.matches('shell-tree-*', self.family_member))

    def test_family_mask_matches_an_adapter_that_wrote_the_same_mask(self) -> None:
        # Адаптер в `for` и зависимый в `requires` пишут одну и ту же маску.
        self.assertTrue(m.matches('shell-tree-*', self.adapter))

    def test_a_plain_slug_in_for_answers_that_slug_as_a_requirement(self) -> None:
        self.assertTrue(m.matches('shell-tree', m.Manifest(slug='x', serves=('shell-tree',))))

    def test_family_mask_does_not_match_a_stranger(self) -> None:
        self.assertFalse(m.matches('shell-tree-*', self.stranger))

    def test_resolve_reports_what_was_not_found(self) -> None:
        found, missing = m.resolve(['shell-tree', 'nothing-*'], [self.plain, self.adapter])
        self.assertEqual(list(found), ['shell-tree'])
        self.assertEqual(missing, ('nothing-*',))

    def test_resolve_returns_every_module_behind_a_mask(self) -> None:
        found, missing = m.resolve(['shell-tree-*'], [self.family_member, self.adapter, self.stranger])
        self.assertEqual({item.slug for item in found['shell-tree-*']}, {'shell-tree-bash', 'bash-adapter'})
        self.assertEqual(missing, ())


if __name__ == '__main__':
    unittest.main()


class AdaptersOfTest(unittest.TestCase):
    """Обратный ход: база называет свой slug и получает своих адаптеров."""

    def setUp(self) -> None:
        self.base = m.Manifest(slug='start-context-claude', events=('session-start',))
        self.by_mask = m.Manifest(slug='craft-snapshot', serves=('start-context-*',))
        self.by_slug = m.Manifest(slug='only-claude', serves=('start-context-claude',))
        self.other_base = m.Manifest(slug='start-context-codex', events=('session-start',))
        self.stranger = m.Manifest(slug='probe')

    def adapters(self, slug: str) -> list[str]:
        everyone = [self.base, self.by_mask, self.by_slug, self.other_base, self.stranger]
        return [found.slug for found in m.adapters_of(slug, everyone)]

    def test_mask_and_exact_slug_both_answer(self) -> None:
        self.assertEqual(self.adapters('start-context-claude'), ['craft-snapshot', 'only-claude'])

    def test_the_other_base_gets_only_the_adapter_whose_mask_covers_it(self) -> None:
        self.assertEqual(self.adapters('start-context-codex'), ['craft-snapshot'])

    def test_a_base_is_not_its_own_adapter(self) -> None:
        self.assertNotIn('start-context-claude', self.adapters('start-context-claude'))

    def test_a_copy_of_the_base_is_covered_by_the_family_mask(self) -> None:
        self.assertEqual(self.adapters('start-context-claude-4'), ['craft-snapshot'])

    def test_the_order_is_by_slug_so_the_glue_is_repeatable(self) -> None:
        shuffled = [self.by_slug, self.by_mask]
        self.assertEqual(
            [found.slug for found in m.adapters_of('start-context-claude', shuffled)],
            ['craft-snapshot', 'only-claude'],
        )
