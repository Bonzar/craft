from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
import runtime_bootstrap as b
import runtime_joint as j


class BootstrapFixture(TestCase):
    def setUp(self):
        tmp=TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name).resolve()
        self.visual=root/'visual'
        self.runtime=root/'runtime'
        self.visual.mkdir()
        self.runtime.mkdir()
        (self.visual/'deploy.lock').touch(mode=0o600)
        self.old={'static':'releases/'+'a'*64,'runtime':'versions/'+'b'*64}
        self.new={'static':'releases/'+'c'*64,'runtime':'versions/'+'d'*64}
        for pair,bytes_ in [(self.old,b'exact old bytes'),(self.new,b'new bytes')]:
            static=self.visual/pair['static']
            runtime=self.runtime/pair['runtime']
            static.mkdir(parents=True)
            runtime.mkdir(parents=True)
            (static/'index.html').write_bytes(bytes_+b' html')
            (runtime/'asset-origin.py').write_bytes(bytes_)
        (self.visual/'current').symlink_to(self.old['static'])
        (self.runtime/'current').symlink_to(self.old['runtime'])
        self.started=self.old
        self.generation=1
        self.legacy=[]
        self.modern=[]
        self.policy=b.Bootstrap('a'*64,'b'*64,sha256(b'exact old bytes').hexdigest(),
            '1'*40,'2'*64,'d'*64,self.legacy_health)

    def pair(self):
        return {'static':str((self.visual/'current').readlink()),
                'runtime':str((self.runtime/'current').readlink())}

    def restart(self):
        self.generation+=1
        self.started=self.pair()

    def legacy_health(self,pair,previous):
        self.assertEqual(pair,self.old)
        self.assertEqual(self.started,self.old)
        self.assertEqual(self.pair(),self.old)
        if previous is not None:
            self.assertNotEqual(str(self.generation),previous)
        self.assertEqual((self.visual/'current/index.html').read_bytes(),b'exact old bytes html')
        self.assertEqual((self.runtime/'current/asset-origin.py').read_bytes(),b'exact old bytes')
        self.legacy.append(self.generation)
        return str(self.generation)

    def run_pair(self, smoke, *, candidate=None):
        return j.run(self.visual,self.runtime,candidate or self.new,validate=lambda p:p.name,
             restart=self.restart,smoke=smoke,bootstrap=self.policy)

    def test_failed_pair_restores_exact_old_assets_and_new_old_process(self):
        def failed(pair):
            self.assertEqual(self.started,self.new)
            raise ValueError('modern is unhealthy')
        with self.assertRaisesRegex(RuntimeError,'old pair restored'):
            self.run_pair(failed)
        self.assertEqual(self.legacy,[1,3])
        self.assertFalse((self.runtime/b.USED).exists())
        self.assertFalse((self.runtime/j.JOURNAL).exists())

    def test_interrupted_first_launch_only_recovers_old_and_denies_wrong_pair(self):
        def interrupted(pair):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.run_pair(interrupted)
        self.assertEqual(self.pair(),self.new)
        self.assertTrue((self.runtime/j.JOURNAL).exists())
        wrong={**self.new,'static':self.old['static']}
        with self.assertRaisesRegex(ValueError,'exact signed pair'):
            self.run_pair(interrupted,candidate=wrong)
        with self.assertRaisesRegex(RuntimeError,'recovered previous Visual; no new activation'):
            self.run_pair(interrupted)
        self.assertEqual(self.started,self.old)
        self.assertEqual(self.legacy,[1,3])
        self.assertFalse((self.runtime/b.USED).exists())

    def test_modern_health_consumes_bootstrap_and_replay_fails(self):
        def modern(pair):
            self.assertEqual(pair,self.new)
            self.assertEqual(self.started,self.new)
            self.modern.append(self.generation)
        self.assertEqual(self.run_pair(modern),self.new)
        self.assertTrue((self.runtime/b.USED).is_file())
        self.assertFalse((self.runtime/j.JOURNAL).exists())
        with self.assertRaisesRegex(ValueError,'already used'):
            self.run_pair(modern)
        self.assertEqual(self.modern,[2])
        self.assertEqual(self.legacy,[1])

    def test_no_bootstrap_on_different_signed_package_or_unproved_legacy(self):
        with self.assertRaises(ValueError):
            self.policy.authorize({'revision':'3'*40,'static_sha':'2'*64},'d'*64)
        (self.runtime/self.old['runtime']/'asset-origin.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'entry hash'):
            self.run_pair(lambda pair:self.fail('must not call modern health'))
        self.assertEqual(self.started,self.old)
        self.assertFalse((self.runtime/j.JOURNAL).exists())


if __name__=='__main__':
    main()
