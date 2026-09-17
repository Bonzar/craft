"""Разбор шапки и поиск по requires."""

import tempfile
import unittest
from pathlib import Path

from jarvis import manifest as m


class ParseTest(unittest.TestCase):
    def test_four_field_header_is_read(self) -> None:
        parsed = m.parse(
            'slug = "lock-irreversible-shell"\n'
            'for = "shell-tree"\n'
            'events = ["pre-tool", "prompt"]\n'
            'requires = ["shell-tree"]\n'
        )
        self.assertEqual(parsed.slug, 'lock-irreversible-shell')
        self.assertEqual(parsed.serves, 'shell-tree')
        self.assertEqual(parsed.events, ('pre-tool', 'prompt'))
        self.assertEqual(parsed.requires, ('shell-tree',))

    def test_header_without_optional_fields_is_valid(self) -> None:
        parsed = m.parse('slug = "probe"\nevents = ["prompt"]\nrequires = []\n')
        self.assertIsNone(parsed.serves)
        self.assertEqual(parsed.requires, ())

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
        self.adapter = m.Manifest(slug='bash-adapter', serves='shell-tree')
        self.stranger = m.Manifest(slug='other')

    def test_plain_slug_matches_by_slug(self) -> None:
        self.assertTrue(m.matches('shell-tree', self.plain))
        self.assertFalse(m.matches('shell-tree', self.stranger))

    def test_plain_slug_does_not_match_a_family_member(self) -> None:
        self.assertFalse(m.matches('shell-tree', self.family_member))

    def test_family_mask_matches_by_slug(self) -> None:
        self.assertTrue(m.matches('shell-tree-*', self.family_member))

    def test_family_mask_matches_an_adapter_by_its_for_field(self) -> None:
        self.assertTrue(m.matches('shell-tree-*', self.adapter))

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
