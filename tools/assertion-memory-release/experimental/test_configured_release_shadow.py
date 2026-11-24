from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import configured_release_shadow as shadow


class Stages(unittest.TestCase):
    def test_projects_share_stages_with_exact_evidence(self):
        for profile, tag in [('base', 'assertion-memory-v0.1.1'),
                             ('visual', 'assertion-memory-visual-v0.1.0')]:
            with self.subTest(profile=profile), TemporaryDirectory() as folder:
                path = Path(folder)/'output'
                env = dict(GITHUB_EVENT_NAME='pull_request', GITHUB_REPOSITORY='Bonzar/craft',
                           GITHUB_BASE_REF='main', GITHUB_RUN_ATTEMPT='1', GITHUB_RUN_ID='42',
                           GITHUB_SHA='a'*40, GITHUB_OUTPUT=str(path), GH_TOKEN='fixture')
                data = dict(tag=tag, revision='b'*40, release_id='1', build_run_id='2',
                            artifact_id='3', archive_sha='c'*64)
                with (patch.object(shadow.original, 'from_tag', return_value=data),
                      patch.object(shadow.original, 'artifact_digest', return_value='c'*64)):
                    shadow.run(Namespace(profile=profile, tag=tag, stage='build'), env)
                    proof = path.read_text().splitlines()[0].split('=',1)[1]
                    env.update(BUILD_PROOF=proof)
                    shadow.run(Namespace(profile=profile, tag=tag, stage='test'), env)
                    with self.assertRaises(ValueError):
                        shadow.run(Namespace(profile=profile, tag=tag, stage='prod'), env)
                    env.update(TEST_PROOF=proof)
                    shadow.run(Namespace(profile=profile, tag=tag, stage='prod'), env)
                    with self.assertRaises(ValueError):
                        shadow.run(Namespace(profile=profile, tag=tag, stage='prod'),
                                   dict(env, GITHUB_RUN_ID='43'))
                    with self.assertRaises(ValueError):
                        shadow.run(Namespace(profile=profile, tag=tag, stage='prod'),
                                   dict(env, GITHUB_EVENT_NAME='workflow_dispatch'))


if __name__ == '__main__':
    unittest.main()
