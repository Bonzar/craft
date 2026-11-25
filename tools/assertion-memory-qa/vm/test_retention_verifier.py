import array
import fcntl
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest

import retention_authority as auth


class Authority(unittest.TestCase):
    def test_components_mount_paths_and_file_system_aliases(self):
        path=b'/srv/assertion-release/test/releases/old'
        self.assertTrue(auth.within(path,path+b'/file (deleted)'))
        self.assertFalse(auth.within(path,path+b'-not-old/file'))
        fs=b'1 0 8:1 / / rw - ext4 /dev/disk rw\n'
        self.assertFalse(auth.mount_hits(fs,path,b'8:1'))
        self.assertTrue(auth.mount_hits(fs+b'2 0 8:1 /unknown /hidden rw - ext4 /dev/disk rw\n',path,b'8:1'))
        self.assertTrue(auth.mount_hits(fs+b'2 0 8:2 '+path+b'/data /hidden rw - ext4 /dev/other rw\n',path,b'8:1'))
        self.assertTrue(auth.mount_hits(fs,path,b'8:2'))

    def test_requires_exact_held_lock_description(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            with (root/'deploy.lock').open('w+b') as locked, (root/'deploy.lock').open('r+b') as wrong:
                with self.assertRaises(ValueError):
                    auth.check_lock(root,locked.fileno())
                fcntl.flock(locked,fcntl.LOCK_EX)
                with self.assertRaises(BlockingIOError):
                    auth.check_lock(root,wrong.fileno())
                fd=os.dup(locked.fileno())
                try:
                    auth.check_lock(root,fd)
                finally:
                    os.close(fd)
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(wrong,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_request_is_bound_to_one_lane_and_exact_one_fd(self):
        msg={'version':1,'lane':'test','active':'a'*40+'-'+'a'*64,'inactive':'b'*40+'-'+'b'*64}
        with tempfile.TemporaryFile() as fd:
            def offer(msg,count):
                a,b=socket.socketpair()
                with a,b:
                    if count:
                        a.sendmsg([json.dumps(msg).encode()+b'\n'],
                                  [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd.fileno()]*count))])
                    else:
                        a.sendall(json.dumps(msg).encode()+b'\n')
                    a.shutdown(socket.SHUT_WR)
                    return auth.receive_request(b)
            decoded, received=offer(msg,1)
            try:
                self.assertEqual(decoded,msg)
                self.assertTrue(os.path.samestat(os.fstat(received),os.fstat(fd.fileno())))
            finally:
                os.close(received)
            with self.assertRaises(ValueError):
                offer({**msg,'lane':'prod'},1)
            with self.assertRaises(ValueError):
                offer({**msg,'inactive':'../prod'},1)
            for n in (0,2):
                with self.assertRaises(ValueError):
                    offer(msg,n)


if __name__=='__main__':
    unittest.main()
