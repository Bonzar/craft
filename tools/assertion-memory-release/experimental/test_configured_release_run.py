"""Exercise the actual runner/composer boundary before Actions is dispatched."""
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch
from configured_release_run import build, deploy_phase, load_registry
import configured_provider_checks as common


class BuildComposition(TestCase):
    def test_passes_profile_objects_and_exports_actual_targets(self):
        profiles, operations = load_registry()
        seen = []
        def original(get, candidates, env, event):
            choices = tuple(candidates)
            self.assertEqual({x.name for x in choices}, {'base','visual'})
            profile = profiles[event['inputs']['profile']]
            seen.append(profile)
            return profile, common.Original(event['inputs']['tag'], 'a'*40,
                '1','2','3','b'*64)
        with TemporaryDirectory() as d, patch.object(common, 'build_selection', original):
            output = Path(d)/'output'
            for name, tag in [('base','assertion-memory-v0.1.1'),('visual','assertion-memory-visual-v0.1.0')]:
                output.write_bytes(b'')
                build({'GITHUB_OUTPUT':str(output)}, {'inputs':{'profile':name,'tag':tag}},None,profiles,operations)
                got=dict(line.split('=',1) for line in output.read_text().splitlines())
                self.assertEqual((got['profile'],got['tag']), (name,tag))
                self.assertEqual((got['test_environment'],got['prod_environment']),
                                 (profiles[name].test.environment,profiles[name].prod.environment))
                self.assertEqual((got['test_concurrency'],got['prod_concurrency']),operations[name].concurrency)
        self.assertEqual(len(seen),2)


class DeployDiagnostics(TestCase):
    def test_phase_logs_only_fixed_label_and_preserves_failure(self):
        stream = StringIO()
        failure = ValueError('synthetic private receiver reply')
        with redirect_stderr(stream), self.assertRaises(ValueError) as caught:
            with deploy_phase('smoke'):
                raise failure
        self.assertIs(caught.exception, failure)
        self.assertEqual(stream.getvalue(), 'Configured release phase failed: smoke\n')

    def test_success_is_silent(self):
        stream = StringIO()
        with redirect_stderr(stream), deploy_phase('unpack'):
            pass
        self.assertEqual(stream.getvalue(), '')


if __name__=='__main__':
    main()
