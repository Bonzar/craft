"""Synthetic package-boundary proof; never uses the user's Mac profile."""
from pathlib import Path
import runpy
from unittest.mock import patch

from . import test_assertion_memory_mac_v2_updater as base_fixture

WRAPPER=Path(__file__).resolve().parents[1]/'tools/assertion-memory-mac-commentary'


class CommentaryMacPackageTest(base_fixture.MacCaptureV2UpdaterTest):
    # Only this subclass's package tests use the changed two-file pins.
    def test_two_file_package_leaves_all_v2_companions_unchanged(self):
        w=runpy.run_path(str(WRAPPER))
        constants=w['configure'].__globals__
        names={'native_capture.py','outbox.py'}
        two={n:self.new[n] for n in names}
        unchanged={n:self.hash(self.old[n]) for n in self.old if n not in names}
        self.assertEqual(set(w['COMMENTARY_PINS']),names)
        guarded={n:(self.hash(self.old[n]),self.hash(self.new[n])) for n in names}
        with patch.dict(constants,COMMENTARY_PINS=guarded,UNCHANGED=unchanged):
            self.globals['source']=lambda _:two
            p=w['configure'](self.globals)
            safe=p['run']
            self.assertEqual(safe(self.home,self.home,mode='plan'),'preflight_ok_two_files_only')
            self.assertEqual(safe(self.home,self.home,mode='apply',quiesced=True,prod=p['PROD_BASE']),
                             'installed_two_files')
            self.same({**self.old,**two})
            self.assertEqual(safe(self.home,self.home,mode='rollback',quiesced=True,no_clear=True),
                             'rolled_back_two_files')
            self.same(self.old)

    def test_wrapper_refuses_drift_in_unmodified_companion(self):
        w=runpy.run_path(str(WRAPPER))
        constants=w['configure'].__globals__
        target='frontier.py'
        with patch.dict(constants,UNCHANGED={target:self.hash(self.old[target])}):
            self.write(self.lib/target,b'# unapproved version',0o644)
            p=w['configure'](self.globals)
            with self.assertRaisesRegex(ValueError,'Unexpected unchanged'):
                p['run'](self.home,self.home,mode='plan')
