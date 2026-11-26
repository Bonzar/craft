from dataclasses import replace
from unittest import TestCase
from transition_witness import Admission, Release
from terminal_verifier import BASE,GRAPH,Process,Snapshot,terminal

class Verify(TestCase):
    uid=986
    old=Release('1'*40,'2'*64,'3'*64,'legacy')
    new=Release('4'*40,'5'*64,'6'*64,'sidecar')
    admission=Admission('42','7'*64,new)

    def fixture(self,phase):
        release=self.new if phase=='restart' else self.old
        p=Process(BASE,345,30,self.uid,release)
        if phase=='restart':
            q=p; ls=((8902,345),(8903,345))
        else:
            q=Process(GRAPH,346,31,self.uid,release); ls=((8901,346),(8903,345))
        before=Snapshot(release,p,q,ls,('42','7'*64,self.old,self.new),'10000000-0000-0000-0000-000000000000',(11,22))
        return dict(admission=self.admission,predecessor=self.old,phase=phase,uid=self.uid,before=before,
                    after=replace(before,journal=None))

    def test_exact_same_pid_candidate_and_markerless_restored_pair(self):
        self.assertEqual(terminal(**self.fixture('restart')),self.new)
        self.assertEqual(terminal(**self.fixture('restore')),self.old)

    def test_reject_stale_spoofed_or_partially_settled_observation(self):
        good=self.fixture('restart')
        after=good['after']
        for updated in (
                dict(after=replace(after,journal=good['before'].journal)),
                dict(after=replace(after,private=replace(after.private,start_ticks=31))),
                dict(after=replace(after,current=self.old)),
                dict(after=replace(after,database_identity=(11,24))),
                dict(after=replace(after,boot_id='20000000-0000-0000-0000-000000000000')),
                dict(after=replace(after,listeners=((8902,346),(8903,345)))),
                dict(after=replace(after,listeners=((8901,347),*after.listeners))),
        ):
            with self.subTest(updated=updated), self.assertRaises(ValueError):
                terminal(**(good|updated))
