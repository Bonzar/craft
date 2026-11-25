import os
from pathlib import Path
import tempfile
import unittest

import release_core as core


class Retention(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        (self.root/'releases').mkdir()
        (self.root/'state').mkdir()
        self.db=self.root/'state'/'memory.sqlite3'
        self.db.write_bytes(b'synthetic state')
        self.old=self.release('a')
        self.new=self.release('b')
        core.swap(self.root,self.old)
        self.events=[]

    def release(self,ch):
        p=self.root/'releases'/(ch*40+'-'+ch*64)
        p.mkdir()
        (p/'.prepared-v1').write_bytes(b'ok\n')
        return p

    def qualified(self,active,inactive):
        self.assertFalse((self.root/core.PENDING).exists())
        self.assertEqual(core.current_target(self.root),str(active))
        self.events.append(('prune',inactive))
        return True

    def hook(self,_script,action,p):
        self.events.append((action,p))

    def test_upgrade_and_old_tag_reinstall(self):
        # The old runtime is available during smoke, not after commit.
        def hook(script,action,path):
            self.assertTrue(self.old.is_dir())
            self.hook(script,action,path)
        core.activate(self.root,self.new,None,hook)
        self.assertTrue(core.prune_inactive_base(self.root,self.new,self.qualified)['current_only'])
        self.assertEqual(self.events,[('restart',self.new),('smoke',self.new),('prune',self.old)])
        self.assertFalse(self.old.exists())
        self.assertEqual(self.db.read_bytes(),b'synthetic state')
        self.old=self.release('a')  # Recovered from immutable artifact, not a stash.
        core.activate(self.root,self.old,None,self.hook)
        core.prune_inactive_base(self.root,self.old,self.qualified)
        self.assertFalse(self.new.exists())
        self.assertTrue(self.old.is_dir())

    def test_failure_restores_before_prune(self):
        def hook(script,action,path):
            self.hook(script,action,path)
            if action=='smoke' and path==self.new:
                raise ValueError('synthetic rejection')
        with self.assertRaisesRegex(ValueError,'synthetic rejection'):
            core.activate(self.root,self.new,None,hook)
        core.prune_inactive_base(self.root,self.old,self.qualified)
        self.assertEqual(self.events,[
            ('restart',self.new),('smoke',self.new),('restore',self.old),('smoke',self.old),('prune',self.new),
        ])
        self.assertTrue(self.old.is_dir())
        self.assertFalse(self.new.exists())

    def test_pending_or_unproven_release_cannot_be_removed(self):
        core.record_activation(self.root,str(self.old),self.new)
        with self.assertRaisesRegex(ValueError,'pending'):
            core.prune_inactive_base(self.root,self.old,self.qualified)
        (self.root/core.PENDING).unlink()
        with self.assertRaisesRegex(ValueError,'unproven'):
            core.prune_inactive_base(self.root,self.old,lambda *_: False)
        self.assertTrue(self.new.is_dir())

    def test_symlink_and_foreign_payload_are_not_followed(self):
        external=self.root/'external'
        external.mkdir()
        (external/'keep').write_bytes(b'synthetic foreign file')
        (self.root/'releases'/('c'*40+'-'+'c'*64)).symlink_to(external)
        visual=self.root/'releases'/('d'*64)
        visual.mkdir()
        (visual/'.prepared-v1').write_bytes(b'ok\n')
        (self.new/'venv').mkdir()
        (self.new/'venv'/'permission-checked-link').symlink_to(external)
        inventory=core.inventory_base_releases(self.root,self.old)
        self.assertEqual(inventory['eligible_pending_host_proof'],1)
        self.assertEqual(inventory['unsafe'],1)
        self.assertEqual(inventory['unknown'],1)
        self.assertFalse(inventory['current_only'])
        report=core.prune_inactive_base(self.root,self.old,self.qualified)
        self.assertFalse(report['current_only'])
        self.assertEqual(report['eligible_pending_host_proof'],0)
        self.assertTrue(visual.is_dir())
        self.assertEqual((external/'keep').read_bytes(),b'synthetic foreign file')
        self.assertTrue((self.root/'releases'/('c'*40+'-'+'c'*64)).is_symlink())


if __name__=='__main__':
    unittest.main()
