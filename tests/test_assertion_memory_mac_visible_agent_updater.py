"""Read-only-source Mac updater acceptance using a fictional profile and local receipts."""
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

from .test_assertion_memory_capture import CaptureTest
from .test_assertion_memory_frontier import FakeRemote

ROOT=Path(__file__).resolve().parents[1]
TOOL=ROOT/'tools/assertion-memory-mac-visible-agent'
# Exact approved for this compatibility package, not a branch or arbitrary old runtime.
PREDECESSOR='ba4e0bf2b06063b02fd260f77cd3397db629efd4'


def load(lib, name):
    spec=importlib.util.spec_from_file_location(name,lib/'__init__.py',submodule_search_locations=[str(lib)])
    pkg=importlib.util.module_from_spec(spec);sys.modules[name]=pkg;spec.loader.exec_module(pkg)
    return tuple(__import__(name+'.'+mod,fromlist=[mod]) for mod in ('native_capture','outbox','frontier'))


def source_pin(repo, name, sha):
    return subprocess.check_output(['git','-C',str(repo),'--no-replace-objects','show',
        f'{sha}:modules/assertion-memory/lib/{name}'],timeout=10)


class VisibleMacUpdaterTest(unittest.TestCase):
    def test_requires_exact_one_file_package_and_explicit_existing_binding(self):
        from unittest.mock import patch
        from .test_assertion_memory_mac_v2_updater import MacCaptureV2UpdaterTest
        f=MacCaptureV2UpdaterTest(methodName='runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        w=runpy.run_path(str(TOOL))
        namespace=f.globals
        name='native_capture.py'
        one={name:(f.hash(f.old[name]),f.hash(f.new[name]))}
        untouched={key:f.hash(raw) for key,raw in f.old.items() if key!=name}
        with patch.dict(w['configure'].__globals__,PINS=one,COMPANIONS=untouched):
            namespace['source']=lambda _:{name:f.new[name]}
            update=w['configure'](namespace)
            with self.assertRaisesRegex(ValueError,'existing PROD'):
                update(f.home,f.home,mode='apply',quiesced=True)
            self.assertEqual(update(f.home,f.home,mode='plan'),'preflight_ok_one_file')
            self.assertEqual(update(f.home,f.home,mode='apply',quiesced=True,existing_prod=True),
                             'installed_one_file')
            f.same({**f.old,name:f.new[name]})
            self.assertEqual(update(f.home,f.home,mode='rollback',quiesced=True,no_clear=True),
                             'rolled_back_one_file')
            f.same(f.old)
            (f.lib/'frontier.py').write_bytes(b'unknown companion')
            with self.assertRaisesRegex(ValueError,'Unexpected unchanged'):
                update(f.home,f.home,mode='plan')

    def test_same_profile_cursor_after_update_and_new_batch_without_replay(self):
        import os
        # On ordinary shallow CI no historical ancestor object is fetched:
        # the unit scope uses the currently tracked fixture and a strict Git blob pin.
        # Operator acceptance explicitly supplies the clean exact old/new checkouts.
        old_repo=os.environ.get('ASSERTION_MAC_UPDATER_OLD_QA_CHECKOUT')
        new_repo=os.environ.get('ASSERTION_MAC_UPDATER_NEW_QA_CHECKOUT')
        if not old_repo or not new_repo:
            self.skipTest('requires two pre-existing verified local source checkouts')
        old_repo,new_repo=Path(old_repo),Path(new_repo)
        w=runpy.run_path(str(TOOL))
        exact=w['configure'](w['load_updater'](new_repo))
        with tempfile.TemporaryDirectory(prefix='fictional-mac-update-') as temp:
            home=Path(temp).resolve()
            module=home/'.codex/jarvis/modules/assertion-memory'
            lib=module/'lib'
            shutil.copytree(ROOT/'modules/assertion-memory/lib',lib)
            prior=source_pin(old_repo,'native_capture.py',PREDECESSOR)
            self.assertEqual(w['blob'](prior),w['PINS']['native_capture.py'][0])
            (lib/'native_capture.py').write_bytes(prior)
            archive=module/'mcp/installed.json';archive.parent.mkdir()
            endpoint='https://fixture.invalid/memory-api-prod';since='2024-01-01T00:00:00Z'
            prefix=hashlib.sha256(('private_base_http\0prod\0'+endpoint+'\0'+since).encode()).hexdigest()
            data=home/'capture-state';state=data/prefix;state.mkdir(parents=True,mode=0o700)
            blob={'harness':'codex','connection':{'url':endpoint,'transport':'private_base_http',
                    'keychain_service':'fictional-example','keychain_account':'fictional-account'},
                  'capture':{'enabled':True,'capture_batch':True,'fresh_since':since,'state_dir':str(data)},
                  'endpoint_sha256':prefix}
            archive.write_text(json.dumps(blob));archive.chmod(0o600)
            lock=state/'worker.lock';lock.touch(mode=0o600)
            code,outbox,frontier=load(lib,'qa_worker_before_update')
            f=CaptureTest(methodName='runTest');f.setUp();self.addCleanup(f.doCleanups)
            f.path.unlink()
            f.append({'type':'session_meta','timestamp':'2025-01-01T00:00:00Z',
                      'payload':{'id':'physical','cwd':str(f.root),'originator':'Codex Desktop','source':'vscode'}})
            cfg={'capture_batch':True}
            remote=FakeRemote()
            old=outbox.Outbox(state/'outbox.sqlite3')
            code.register(old,f.raw,fresh_since=since)
            f.turn('acknowledge before upgrade','first','u1',trigger='user')
            f.assistant('one answer','first-a')
            c=frontier.Coordinator(old,remote)
            c.observe('physical',code.poll(old,'physical',cfg))
            self.assertEqual(c.tick()['acknowledged'],2)
            cursor=old.session('physical')['cursor']
            old.close()
            readback=archive.read_bytes()
            self.assertEqual(exact(home,new_repo,mode='plan'),'preflight_ok_one_file')
            with lock.open() as handle:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError,'still holds'):
                    exact(home,new_repo,mode='apply',quiesced=True,existing_prod=True)
            self.assertEqual(exact(home,new_repo,mode='apply',quiesced=True,existing_prod=True),'installed_one_file')
            self.assertEqual(readback,archive.read_bytes())
            self.assertEqual(w['blob']((lib/'native_capture.py').read_bytes()),w['PINS']['native_capture.py'][1])
            updated,queue,coordinator=load(lib,'qa_worker_after_update')
            new=queue.Outbox(state/'outbox.sqlite3')
            try:
                self.assertEqual(new.session('physical')['cursor'],cursor)
                cc=coordinator.Coordinator(new,remote)
                cc.observe('physical',updated.poll(new,'physical',cfg))
                self.assertEqual(cc.tick()['acknowledged'],0)
                f.turn('only new user turn','second','u2',trigger='user')
                f.commentary('new partial','partial',phase='partial_answer')
                f.assistant('new final','second-a')
                cc.observe('physical',updated.poll(new,'physical',cfg))
                self.assertEqual(cc.tick()['acknowledged'],3)
                self.assertEqual(cc.tick()['acknowledged'],0)
                batches=[req for op,req in remote.calls if op=='memory_capture_batch']
                self.assertEqual([len(req['messages']) for req in batches],[2,3])
                self.assertGreater(new.session('physical')['cursor'],cursor)
            finally:new.close()
            self.assertEqual(exact(home,new_repo,mode='rollback',quiesced=True,no_clear=True),'rolled_back_one_file')
            self.assertEqual((lib/'native_capture.py').read_bytes(),prior)
            self.assertEqual(readback,archive.read_bytes())
