import re
import unittest
from pathlib import Path

from lifecycle import ROOT, WORKFLOW, find_artifact, identify, verify_receipt

RELEASE = '42'
SHA = 'a'*40
DIGEST = 'b'*64
TAG = 'assertion-memory-v1.2.3'
NAME = f'assertion-memory-base-release-{RELEASE}-{SHA}'


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        release = {'id':42, 'draft':False, 'immutable':True, 'published_at':'fixture', 'tag_name':TAG}
        artifact = {'id':43, 'name':NAME, 'expired':False, 'size_in_bytes':1024,
                    'digest':'sha256:'+DIGEST, 'workflow_run':{'id':44, 'head_sha':SHA}}
        run = {'id':44, 'workflow_id':45, 'event':'release', 'conclusion':'success',
               'run_attempt':1, 'head_sha':SHA, 'head_repository':{'full_name':'Bonzar/craft'}}
        self.data = {
            f'{ROOT}/releases/{RELEASE}':release,
            f'{ROOT}/git/ref/tags/{TAG}': {'ref':'refs/tags/'+TAG, 'object':{'type':'tag','sha':'c'*40}},
            f'{ROOT}/git/tags/{"c"*40}':{'object':{'type':'commit','sha':SHA}},
            f'{ROOT}/compare/{SHA}...main':{'status':'ahead'},
            f'{ROOT}/actions/artifacts?per_page=2&name={NAME}':{'total_count':1,'artifacts':[artifact]},
            f'{ROOT}/actions/runs/44':run,
            f'{ROOT}/actions/workflows/{WORKFLOW}':{'id':45},
        }
        self.release = release
        self.artifact = artifact
        self.run = run

    def test_workflow_release_filter_separates_base_and_visual_refs(self):
        workflow = Path(__file__).resolve().parents[2] / '.github/workflows' / WORKFLOW
        line, = (line for line in workflow.read_text().splitlines()
                 if "if:" in line and "startsWith(github.ref," in line)
        clause = r"(!?)startsWith\(github\.ref, '([^']+)'\)"
        self.assertRegex(line, clause + r" && " + clause)
        conditions = re.findall(clause, line)
        def matches(ref):
            return all(ref.lower().startswith(prefix.lower()) != bool(negate)
                       for negate, prefix in conditions)
        self.assertTrue(matches('refs/tags/assertion-memory-v0.1.1'))
        self.assertFalse(matches('refs/tags/assertion-memory-visual-v0.1.0'))
        self.assertFalse(matches('refs/tags/unrelated-v0.1.0'))
        graph = workflow.with_name('assertion-memory-release-graph.yml')
        gate, = (line for line in graph.read_text().splitlines()
                 if "if:" in line and "ASSERTION_MEMORY_NATIVE_BASE_TEST == " in line)
        clause = clause.replace(r'github\.ref', r'needs\.release\.outputs\.tag')
        self.assertRegex(gate, clause + r" && " + clause)
        conditions = re.findall(clause, gate)
        self.assertTrue(matches('assertion-memory-v0.1.1'))
        self.assertFalse(matches('assertion-memory-visual-v0.1.0'))

    def test_original_release_and_artifact(self):
        self.assertEqual(identify(self.data.__getitem__, RELEASE),(TAG,SHA))
        self.assertEqual(find_artifact(self.data.__getitem__,RELEASE,SHA),('43',DIGEST))

    def test_reject_mutable_draft_fork_and_nonmain(self):
        for key in ('immutable','draft'):
            with self.subTest(key=key):
                old=self.release[key]
                self.release[key]=not old
                with self.assertRaises(ValueError):
                    identify(self.data.__getitem__,RELEASE)
                self.release[key]=old
        self.data[f'{ROOT}/compare/{SHA}...main']['status']='diverged'
        with self.assertRaisesRegex(ValueError,'ancestor'):
            identify(self.data.__getitem__,RELEASE)

    def test_reject_other_workflow_attempt_failed_retarg_and_duplicate(self):
        for key, value in (('workflow_id',4),('event','workflow_dispatch'),('run_attempt',2),
                           ('conclusion','failure'),('head_sha','c'*40)):
            with self.subTest(key=key):
                old=self.run[key]
                self.run[key]=value
                with self.assertRaisesRegex(ValueError,'original'):
                    find_artifact(self.data.__getitem__,RELEASE,SHA)
                self.run[key]=old
        self.artifact['expired']=True
        with self.assertRaises(ValueError):
            find_artifact(self.data.__getitem__,RELEASE,SHA)
        self.data[f'{ROOT}/actions/artifacts?per_page=2&name={NAME}']['total_count']=2
        with self.assertRaisesRegex(ValueError,'exactly one'):
            find_artifact(self.data.__getitem__,RELEASE,SHA)

    def test_receipt_every_field_bound(self):
        approved={'release_id':'42','artifact_id':'43','revision':SHA,'sha256':DIGEST}
        verify_receipt(approved,approved)
        for k in approved:
            with self.subTest(key=k), self.assertRaisesRegex(ValueError,'TEST receipt'):
                verify_receipt(approved,{**approved,k:'wrong'})


if __name__ == '__main__':
    unittest.main()
