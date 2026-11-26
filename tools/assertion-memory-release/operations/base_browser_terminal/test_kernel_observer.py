from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from kernel_observer import _observe,_tcp,start_ticks,BASE,GRAPH,UID
from terminal_lifecycle import TerminalRequest
from test_terminal_verifier import Verify

class Kernel(TestCase):
    def test_exact_safe_socket_parser_and_stable_pid_tokens(self):
        self.assertEqual(start_ticks('42 (tricky ) name) S '+'0 '*18+'304',42),304)
        with self.assertRaises(ValueError):
            start_ticks('43 (name) S '+'0 '*18+'304',42)
        def tcp(address,pid,port=8902):
            return f'x\n 0: {address}:{port:04X} 00000000:0000 0A 0:0 0:0 0 {pid} 0 305\n'
        self.assertEqual(_tcp(tcp('0100007F',UID)),{8902:305})
        self.assertEqual(_tcp(tcp('00000000',UID,9999)),{})
        for address,uid in [('00000000',UID),('0100007F',0),('00000000000000000000000000000000',UID)]:
            with self.assertRaises(ValueError):
                _tcp(tcp(address,uid))

    def test_same_pid_owns_both_new_sockets_old_graph_must_not_run(self):
        f=Verify()
        req=TerminalRequest(f.admission,f.old,'restart')
        sample=f.fixture('restart')['before']
        with TemporaryDirectory() as tmp:
            proc=Path(tmp)
            boot=proc/'sys/kernel/random/boot_id'
            boot.parent.mkdir(parents=True)
            boot.write_text(sample.boot_id+'\n')
            pid=proc/'345'; pid.mkdir()
            (pid/'stat').write_text('345 (fixture) S '+'0 '*18+'30')
            net=proc/'net'; net.mkdir()
            (net/'tcp6').write_text('header\n')
            rows=''.join(f' {n}: 0100007F:{n:04X} 00000000:0000 0A 0:0 0:0 0 {UID} 0 {n}\n'
                         for n in (8902,8903))
            (net/'tcp').write_text('header\n'+rows)
            cases=((0,{8902,8903},True),(345,{8902,8903},False),(0,{8903},False))
            for old_graph,owned,expected in cases:
                with patch('kernel_observer._process',return_value=(sample.private,owned)):
                    get=lambda unit:345 if unit==BASE else old_graph
                    bound=lambda req:(sample.current,sample.journal)
                    if expected:
                        self.assertEqual(_observe(proc,req,get,bound),sample)
                    else:
                        with self.assertRaises(ValueError):
                            _observe(proc,req,get,bound)

    def test_process_kernel_exe_must_equal_exact_current_venv(self):
        import os
        from kernel_observer import _process
        f=Verify()
        release=f.new
        with TemporaryDirectory() as tmp:
            proc=Path(tmp)
            physical=proc/'physical'
            python=physical/'venv/bin/python'
            python.parent.mkdir(parents=True)
            python.write_text('fixture')
            role='prototypes.assertion_memory.http_server'
            process=proc/'345'
            process.mkdir()
            (process/'stat').write_text('345 (fixture) S '+'0 '*18+'30')
            (process/'status').write_text('Uid: 986 986 986 986\n')
            (process/'cgroup').write_text('0::/user/'+BASE+'\n')
            (process/'cwd').symlink_to(physical,target_is_directory=True)
            (process/'exe').symlink_to(python)
            (process/'cmdline').write_text('\0'.join([str(python),'-m',role,'--config',
                '/srv/assertion-release/production/state/http.json','']))
            (process/'ns').mkdir()
            (process/'ns/net').touch()
            (proc/'self/ns').mkdir(parents=True)
            os.link(process/'ns/net',proc/'self/ns/net')
            (process/'fd').mkdir()
            (process/'fd/12').symlink_to('socket:[8903]')
            self.assertEqual(_process(proc,pid=345,unit=BASE,release=release,physical=physical)[1],{8903})
            wrong=proc/'wrong'; wrong.touch()
            (process/'exe').unlink()
            (process/'exe').symlink_to(wrong)
            with self.assertRaisesRegex(ValueError,'wrong protected process executable'):
                _process(proc,pid=345,unit=BASE,release=release,physical=physical)
