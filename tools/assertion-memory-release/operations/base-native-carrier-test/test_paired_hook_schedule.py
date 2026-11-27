from unittest import TestCase
from paired_hook_schedule import BASE, GRAPH, Contract, State, ordered_restart


class Controller:
    def __init__(self):
        self.new='a'*40+'-'+'b'*64
        self.old='c'*40+'-'+'d'*64
        self.bind(self.old,Contract.LEGACY)
        self.graph=(10,'old-graph-mainpid')
        self.base=(11,'old-base-mainpid')
        self.listener=self.graph
        self.actions=[]
        self.stuck=False
        self.bad_graph=False
        self.bad_base=False

    def bind(self,release,contract):
        self.revision,self.contract=release,contract

    def current(self):
        return self.revision

    def wait_for(self,release,predicate):
        assert release==self.revision
        return State(self.graph,self.base,self.listener,self.base)

    def preflight(self,release):
        assert release==self.revision
        self.actions.append(('preflight',))

    def stop_exact_pair(self,graph,base):
        assert (graph,base)==(GRAPH,BASE)
        self.actions.append(('stop',graph,base))
        self.graph=self.base=None
        if not self.stuck:
            self.listener=None

    def start_exact(self,unit):
        self.actions.append(('start',unit))
        if unit==GRAPH:
            self.graph=(20,self.revision)
            if self.contract is Contract.LEGACY:
                assert self.listener is None
                self.listener=self.graph
        elif unit==BASE:
            self.base=(21,self.revision)
            if self.contract is Contract.NATIVE:
                assert self.listener is None
                self.listener=self.base

    def attest_graph(self,identity,release,contract):
        return identity==(20,release) and contract is self.contract and not self.bad_graph

    def attest_base(self,identity,release,contract):
        return identity==(21,release) and contract is self.contract and not self.bad_base


class PairedLifecycle(TestCase):
    def test_legacy_to_native_and_inverse_both_stop_before_bind(self):
        io=Controller()
        io.bind(io.new,Contract.NATIVE)
        current=ordered_restart(io,io.new,Contract.NATIVE)
        self.assertEqual(current.port8891,current.base)
        io.bind(io.old,Contract.LEGACY)
        restored=ordered_restart(io,io.old,Contract.LEGACY)
        self.assertEqual(restored.port8891,restored.graph)
        self.assertEqual(restored.port8893,restored.base)
        self.assertEqual([step[0] for step in io.actions],['preflight','stop','start','start']*2)

    def test_old_socket_surviving_stop_never_starts_either_unit(self):
        io=Controller(); io.stuck=True; io.bind(io.new,Contract.NATIVE)
        with self.assertRaisesRegex(ValueError,'readiness'):
            ordered_restart(io,io.new,Contract.NATIVE)
        self.assertEqual([x[0] for x in io.actions],['preflight','stop'])

    def test_unproven_standby_never_starts_base(self):
        io=Controller(); io.bad_graph=True; io.bind(io.new,Contract.NATIVE)
        with self.assertRaisesRegex(ValueError,'graph_unit'):
            ordered_restart(io,io.new,Contract.NATIVE)
        self.assertEqual([x[0] for x in io.actions],['preflight','stop','start'])

    def test_failed_native_then_receiver_selects_old_archive_and_restores(self):
        io=Controller(); io.bad_base=True; io.bind(io.new,Contract.NATIVE)
        with self.assertRaisesRegex(ValueError,'base_unit'):
            ordered_restart(io,io.new,Contract.NATIVE)
        io.bind(io.old,Contract.LEGACY); io.bad_base=False
        restored=ordered_restart(io,io.old,Contract.LEGACY)
        self.assertEqual(restored.port8891,(20,io.old))
        self.assertEqual(restored.port8893,(21,io.old))

    def test_real_activation_journal_reselects_and_restores_previous_exact_bytes(self):
        import sys
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from importlib.util import module_from_spec, spec_from_file_location
        module_name='_paired_test_release_core'
        # Test lives under operations/<name>/ in the exact repository.
        core=Path(__file__).resolve().parents[2]/'release_core.py'
        spec=spec_from_file_location(module_name,core)
        module=module_from_spec(spec); sys.modules[module_name]=module
        self.addCleanup(sys.modules.pop,module_name)
        spec.loader.exec_module(module)
        activate, swap, PENDING=module.activate,module.swap,module.PENDING
        io=Controller()
        with TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            releases=root/'releases'; releases.mkdir()
            old=releases/io.old; new=releases/io.new
            for path,specimen in ((old,b'Graph serves'),(new,b'Base serves')):
                path.mkdir()
                (path/'.prepared-v1').write_bytes(b'ok\n')
                (path/'specimen').write_bytes(specimen)
            swap(root,old)
            events=[]
            def installed_hook(_hook,action,release):
                self.assertEqual((root/'current').resolve(),release)
                expected=Contract.LEGACY if release==old else Contract.NATIVE
                if action in ('restart','restore'):
                    io.bind(release.name,expected)
                    ordered_restart(io,release.name,expected)
                else:
                    self.assertEqual(action,'smoke')
                    events.append((action,release.name,io.listener))
                    if release==new:
                        raise ValueError('candidate_smoke_failed')
            with self.assertRaisesRegex(ValueError,'candidate_smoke_failed'):
                activate(root,new,root/'service-hook.sh',installed_hook)
            self.assertEqual((root/'current').resolve(),old)
            self.assertFalse((root/PENDING).exists())
            self.assertEqual((old/'specimen').read_bytes(),b'Graph serves')
            self.assertEqual(events,[('smoke',io.new,(21,io.new)),('smoke',io.old,(20,io.old))])
