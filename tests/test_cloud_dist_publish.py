"""Публикация точного dist в отдельный main, без сетевых учётных данных."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from . import test_cloud_probe_dist as probe


PUBLISHER = Path(__file__).resolve().parents[1] / 'tools/publish-cloud-dist'
MANIFEST = probe.MANIFEST


class CloudDistPublishTest(unittest.TestCase):
    git = probe.CloudProbeDistTest.git
    build = probe.CloudProbeDistTest.build

    def setUp(self):
        probe.CloudProbeDistTest.setUp(self)
        self.remote = self.root / 'target.git'
        subprocess.run(['git', 'init', '--bare', '-q', '--initial-branch=main',
                        str(self.remote)], check=True)
        done = self.build(runtime_root='/workspace/craft-codex-dist')
        self.assertEqual(done.returncode, 0, done.stderr)

    def publish(self, *extra):
        self.assertTrue(PUBLISHER.is_file(), 'Нет публикации dist в отдельный репозиторий')
        return subprocess.run([
            sys.executable, str(PUBLISHER), '--dist', str(self.output),
            '--source', str(self.source), '--ref', self.commit,
            '--remote', str(self.remote), *extra,
        ], capture_output=True, text=True, check=False)

    def target(self, *args):
        return subprocess.check_output(['git', '--git-dir', str(self.remote), *args],
                                       text=True).strip()

    def test_publish_bootstraps_main_with_only_built_files_and_provenance(self):
        (self.output / '.env').write_text('DO-NOT-PUBLISH')
        done = self.publish()
        self.assertEqual(done.returncode, 0, done.stderr)
        manifest = json.loads((self.output / MANIFEST).read_text())
        expected = set(manifest['files']) | {MANIFEST}
        self.assertEqual(set(self.target('ls-tree', '-r', '--name-only', 'main').splitlines()),
                         expected)
        self.assertEqual(self.target('show', 'main:cloud-dist.json'),
                         (self.output / MANIFEST).read_text().strip())
        self.assertIn(self.commit, self.target('log', '-1', '--format=%B', 'main'))

    def test_same_build_is_a_noop(self):
        self.assertEqual(self.publish().returncode, 0)
        first = self.target('rev-parse', 'main')
        done = self.publish()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.target('rev-parse', 'main'), first)
        self.assertIn('без изменений', done.stdout)

    def test_next_build_replaces_the_complete_tree_and_preserves_history(self):
        # Имитируем устаревший артефакт, который следующая публикация удалит.
        self.git('remote', 'add', 'target', str(self.remote))
        self.git('push', '-q', 'target', 'HEAD:main')
        previous = self.target('rev-parse', 'main')
        done = self.publish()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.target('rev-parse', 'main^'), previous)
        self.assertNotIn('modules/', self.target('ls-tree', '-r', '--name-only', 'main'))

    def test_corrupt_build_does_not_change_main_and_reports_reason(self):
        self.assertEqual(self.publish().returncode, 0)
        previous = self.target('rev-parse', 'main')
        (self.output / 'AGENTS.md').write_text('broken')
        done = self.publish()
        self.assertEqual(done.returncode, 1)
        self.assertIn('AGENTS.md', done.stderr)
        self.assertEqual(self.target('rev-parse', 'main'), previous)

    def test_mismatched_source_is_not_published(self):
        manifest_path = self.output / MANIFEST
        manifest = json.loads(manifest_path.read_text())
        manifest['source_commit'] = 'a' * 40
        manifest_path.write_text(json.dumps(manifest))
        done = self.publish()
        self.assertEqual(done.returncode, 1)
        self.assertIn('source_commit', done.stderr)

    def test_stale_main_is_not_published(self):
        upstream = self.root / 'upstream.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(upstream)], check=True)
        self.git('remote', 'add', 'origin', str(upstream))
        (self.source / 'AGENTS.md').write_text('new source')
        self.git('add', '.')
        self.git('-c', 'user.name=tests', '-c', 'user.email=tests@example.invalid',
                 'commit', '-qm', 'new source')
        self.git('push', '-q', 'origin', 'HEAD:main')
        done = self.publish('--require-current-main')
        self.assertEqual(done.returncode, 1)
        self.assertIn('main', done.stderr)
        self.assertEqual(self.target('for-each-ref', '--format=%(refname)', 'refs/heads'), '')


if __name__ == '__main__':
    unittest.main()
