"""Коммиты в папку одним видом для git и arc: что ставили и что лежит на удалённом.

Git проверяется настоящим: два репозитория, origin с main и клон. Arc в тестах
подставной (`fake_arc.py`) — настоящий живёт только в смонтированной Аркадии.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jarvis import vcs

FAKE_ARC = Path(__file__).with_name('fake_arc.py')


def git(where: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(where), *args],
                          capture_output=True, text=True, check=True).stdout


def commit(where: Path, message: str) -> str:
    git(where, 'add', '-A')
    git(where, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '-m', message)
    return git(where, 'rev-parse', 'HEAD').strip()


def write(path: Path, text: str = '#\n') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


class GitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.origin = self.root / 'origin'
        self.origin.mkdir()
        git(self.origin, 'init', '-q', '-b', 'main')
        write(self.origin / 'modules' / 'a' / 'module.toml')
        write(self.origin / 'README.md')
        self.first = commit(self.origin, 'первый')
        self.clone = self.root / 'клон'
        git(self.root, 'clone', '-q', str(self.origin), str(self.clone))

    def test_detect_finds_git_from_a_subfolder(self) -> None:
        found = vcs.detect(self.clone / 'modules' / 'a')
        self.assertIsInstance(found, vcs.Git)

    def test_version_is_the_last_commit_into_the_paths(self) -> None:
        write(self.clone / 'README.md', 'другое\n')
        commit(self.clone, 'мимо папки')
        version = vcs.Git(self.clone, ('modules',)).version('HEAD')
        self.assertEqual(version.commit, self.first)

    def test_a_folder_without_commits_has_no_version(self) -> None:
        self.assertIsNone(vcs.Git(self.clone, ('нет такой',)).version('HEAD'))

    def test_remote_brings_the_head_of_main_with_its_objects(self) -> None:
        write(self.origin / 'modules' / 'a' / 'новое.md')
        head = commit(self.origin, 'в main')
        found = vcs.Git(self.clone, ('modules',))
        self.assertEqual(found.remote(), head)
        self.assertEqual(found.version(head).commit, head)

    def test_changes_are_counted_only_inside_the_paths(self) -> None:
        write(self.origin / 'README.md', 'другое\n')
        outside = commit(self.origin, 'мимо')
        found = vcs.Git(self.clone, ('modules',))
        found.remote()
        self.assertEqual(found.changed(self.first, outside), [])
        write(self.origin / 'modules' / 'a' / 'новое.md')
        inside = commit(self.origin, 'внутрь')
        found.remote()
        self.assertEqual(found.changed(self.first, inside), ['modules/a/новое.md'])

    def test_a_branch_commit_is_not_behind_main(self) -> None:
        git(self.clone, 'checkout', '-q', '-b', 'ветка')
        write(self.clone / 'modules' / 'a' / 'ветка.md')
        branch = commit(self.clone, 'ветка')
        write(self.origin / 'modules' / 'a' / 'новое.md')
        head = commit(self.origin, 'в main')
        found = vcs.Git(self.clone, ('modules',))
        found.remote()
        self.assertFalse(found.behind(branch, head, None, None))
        self.assertTrue(found.behind(self.first, head, None, None))

    def test_an_unreachable_origin_is_an_error_with_a_reason(self) -> None:
        shutil.rmtree(self.origin)
        with self.assertRaises(vcs.VcsError) as caught:
            vcs.Git(self.clone, ('modules',)).remote()
        self.assertIn('ls-remote', str(caught.exception))

    def test_folder_commit_names_the_last_commit_into_the_folder(self) -> None:
        write(self.clone / 'README.md', 'другое\n')
        commit(self.clone, 'мимо папки')
        self.assertEqual(vcs.folder_commit(self.clone / 'modules' / 'a'), self.first)


class ArcTest(unittest.TestCase):
    """Arc подставной: сценарий в файле, `arc` на PATH."""

    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.mount = self.root / 'маунт'
        self.folder = self.mount / 'junk' / 'ai' / 'modules' / 'm'
        self.folder.mkdir(parents=True)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        arc = bin_dir / 'arc'
        arc.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_ARC}" "$@"\n', encoding='utf-8')
        arc.chmod(0o755)
        self.scenario = self.root / 'сценарий.json'
        self.set_scenario({})
        self.enterContext(mock.patch.dict(os.environ, {
            'PATH': f'{bin_dir}{os.pathsep}{os.environ.get("PATH", "")}',
            'FAKE_ARC': str(self.scenario),
        }))

    def set_scenario(self, extra: dict) -> None:
        self.scenario.write_text(json.dumps({'root': str(self.mount), 'trunk': 't2', **extra}),
                                 encoding='utf-8')

    def test_detect_finds_arc_inside_the_mount(self) -> None:
        self.assertIsInstance(vcs.detect(self.folder), vcs.Arc)

    def test_outside_any_repository_there_is_nothing(self) -> None:
        plain = self.root / 'просто папка'
        plain.mkdir()
        self.assertIsNone(vcs.detect(plain))
        self.assertIsNone(vcs.folder_commit(plain))

    def test_version_and_remote_come_from_arc(self) -> None:
        self.set_scenario({'log': {'HEAD': ['c1', '2026-10-01T10:00:00+03:00'],
                                   't2': ['c2', '2026-10-05T10:00:00+03:00']}})
        found = vcs.Arc(self.folder)
        self.assertEqual(found.remote(), 't2')
        self.assertEqual(found.version('t2').commit, 'c2')
        self.assertEqual(vcs.folder_commit(self.folder), 'c1')

    def test_trunk_is_ahead_when_its_commit_into_the_folder_is_newer(self) -> None:
        found = vcs.Arc(self.folder)
        old = vcs.Version('c1', '2026-10-01T10:00:00+03:00')
        new = vcs.Version('c2', '2026-10-05T10:00:00+03:00')
        self.assertTrue(found.behind('c1', 't2', old, new))
        self.assertFalse(found.behind('c2', 't2', new, old))

    def test_a_failed_fetch_is_an_error_with_a_reason(self) -> None:
        self.set_scenario({'fetch_fails': True})
        with self.assertRaises(vcs.VcsError) as caught:
            vcs.Arc(self.folder).remote()
        self.assertIn('сеть недоступна', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
