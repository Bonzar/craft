from contextlib import nullcontext
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import fixed_source_installer as ops

class FixedInstaller(TestCase):
    def test_exact_two_target_inverse_recovers_half_completed_pair(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            paths=[root/'guard',root/'hook']
            old=b'original = True\n'; new=b'candidate = True\n'
            for path in paths:
                path.write_bytes(old); path.chmod(0o644)
            record=[]
            bundle=tuple(ops.VerifiedSource(str(path),0o644,old,new) for path in paths)
            pin='c'*64
            def finish(value):
                self.assertEqual(record.pop(),value)
            def fake_owner(fd,uid,gid):
                return
            actual_regular=ops._regular_at
            def fixture_regular(fd,name,mode):
                # In CI user may not be root; use disposable expected-file
                # pin only. Separate tests check denied writer scope.
                item=root/name
                self.assertEqual(item.stat().st_mode&0o777,mode)
                return item.read_bytes()
            args=dict(bundle=bundle,immutable_bundle_sha=pin,
                verify_bundle=lambda got,ordered:got==pin and len(ordered)==2,
                release_lease=nullcontext,write_recovery=record.append,
                read_recovery=lambda:record[0] if record else None,
                finish_recovery=finish,namespace_preflight=lambda:True)
            with (patch.object(ops,'_TARGETS',tuple(str(p) for p in paths)),
                  patch.object(ops,'KNOWN',{str(p):sha256(old).hexdigest() for p in paths}),
                  patch.object(ops,'_open_parent',lambda path:os.open(root,os.O_DIRECTORY)),
                  patch.object(ops,'_regular_at',side_effect=fixture_regular),
                  patch.object(ops.os,'getuid',return_value=0),
                  patch.object(ops.os,'geteuid',return_value=0),
                  patch.object(ops.os,'fchown',side_effect=fake_owner)):
                rename=os.replace
                def fail_second(staged,target,**kwargs):
                    if target=='hook': raise OSError('fixture split write')
                    return rename(staged,target,**kwargs)
                with patch.object(ops.os,'replace',side_effect=fail_second):
                    with self.assertRaises(OSError):ops.apply_exact(**args)
                self.assertEqual(record,[pin])
                self.assertEqual([p.read_bytes() for p in paths],[new,old])
                with self.assertRaises(ValueError): ops.apply_exact(**args)
                ops.apply_exact(**args,inverse=True)
                self.assertEqual(record,[])
                self.assertEqual([p.read_bytes() for p in paths],[old,old])
                self.assertEqual([p.stat().st_mode&0o777 for p in paths],[0o644]*2)

    def test_wrong_role_or_no_exact_fixed_bundle(self):
        kwargs=dict(bundle=(),immutable_bundle_sha='c'*64,verify_bundle=lambda *_:True,
                    release_lease=nullcontext,write_recovery=lambda *_:None,
                    read_recovery=lambda:None,finish_recovery=lambda *_:None,
                    namespace_preflight=lambda:True)
        with patch.object(ops.os,'geteuid',return_value=986):
            with self.assertRaisesRegex(ValueError,'fixed protected root installer'):
                ops.apply_exact(**kwargs)
        with patch.object(ops.os,'getuid',return_value=0),patch.object(ops.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(ValueError,'missing exact two-source'):
                ops.apply_exact(**kwargs)
