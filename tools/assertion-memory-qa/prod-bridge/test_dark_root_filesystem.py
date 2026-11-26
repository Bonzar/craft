"""Extra hosted proof using real disposable filesystem and fsync/rename."""
from contextlib import ExitStack,nullcontext
from hashlib import sha256
import importlib
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

OPS=Path(__file__).resolve().parents[3]/'tools/assertion-memory-release/operations/base_browser_terminal'
sys.path.insert(0,str(OPS))
import sealed_host_install_bundle as bundle
import dark_install_transaction as txn
import dark_root_filesystem as fs
sys.path.pop(0)

class DarkRootRealFS(TestCase):
    def test_real_rename_fault_leaves_durable_inverse_and_recovers_only_exact_paths(self):
        with TemporaryDirectory(prefix='base-dark-ops-test-') as raw, ExitStack() as patches:
            work=Path(raw)
            mount=str(work/'modules')+'/'
            targets={str(work/Path(p).name):value for p,value in bundle.STAGED.items()}
            modules={mount+Path(p).name:value for p,value in bundle.MOUNTED.items()}
            before={p:(f'# old {p}'+chr(10)).encode() for p in targets}
            installed=targets|modules
            images={name:(f'# reviewed {name}'+chr(10)).encode() for _,name in installed.values()}
            pins={k:sha256(v).hexdigest() for k,v in images.items() if not k.startswith('stock-compiled-')}
            outputs={k:sha256(images[k]).hexdigest() for k in bundle.OUTPUT_PINS}
            stock={p:sha256(data).hexdigest() for p,data in before.items()}
            for p,body in before.items():
                Path(p).write_bytes(body);Path(p).chmod(targets[p][0])

            def regular(fd,name,mode):
                # Fixture uses runner ownership; real production verifier remains root strict.
                statinfo=os.stat(name,dir_fd=fd,follow_symlinks=False)
                self.assertEqual(statinfo.st_mode&0o777,mode)
                d=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
                with os.fdopen(d,'rb') as stream:return stream.read()
            patches.enter_context(patch.object(fs.os,'fchown',return_value=None))
            patches.enter_context(patch.object(fs,'_open_parent',side_effect=lambda p:os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)))
            patches.enter_context(patch.object(fs,'_open_dir',side_effect=lambda p,mode:os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)))
            patches.enter_context(patch.object(fs,'_regular_at',side_effect=regular))
            patches.enter_context(patch.object(fs,'existing_root_install_lease',return_value=nullcontext()))
            for module, values in ((txn,dict(MOUNT=mount,MOUNTED=modules,STAGED=targets)),
                                  (bundle,dict(MOUNT=mount,MOUNTED=modules,STAGED=targets,STOCK_PINS=stock,OUTPUT_PINS=outputs)),
                                  (fs,dict(MOUNT=mount,MOUNT_DIR=Path(mount),STAGED=targets,
                                           RECOVERY=work/'inverse',MARKER=work/'must-not-exist',
                                           NATIVE=work/'native-journal',STATUS_PENDING=()))):
                for k,v in values.items():
                    patches.enter_context(patch.object(module,k,v))
            manifest=bundle.make_manifest(source_commit='1'*40,exact_source_digest_map=pins)
            io=fs.RootDarkIO()
            args=dict(manifest=manifest,source_commit='1'*40,reviewed_pins=pins,images=images,protected_io=io)
            actual_replace=os.replace
            counter=0
            def fail_after_third(*args,**kw):
                nonlocal counter
                counter+=1
                actual_replace(*args,**kw)
                if counter==3:raise OSError('injected failure after actual rename and before calling fsync')
            # Fail right after actual rename; this fixture never leaves the sandbox temp directory.
            with patch.object(fs.os,'replace',side_effect=fail_after_third):
                with self.assertRaises(OSError):txn.run(**args)
            self.assertEqual(io.recovery(),sha256(manifest).hexdigest())
            self.assertTrue(any(Path(p).read_bytes()!=body for p,body in before.items()))
            self.assertEqual(txn.run(**args,inverse=True),'stock-restored')
            self.assertFalse(Path(mount).exists())
            self.assertFalse((work/'inverse').exists())
            self.assertTrue(all(Path(p).read_bytes()==body for p,body in before.items()))
            self.assertEqual(txn.run(**args),'source-dark-ready')
            self.assertEqual(txn.run(**args,inverse=True),'stock-restored')
