from unittest import TestCase
from root_run_records import RECORDS,record
from transition_witness import Release,Admission

class RunIsolation(TestCase):
    def test_successor_records_cannot_pick_previous_attempt_or_phase(self):
        r=Release('a'*40,'b'*64,'c'*64,'legacy')
        a=Admission('1','d'*64,r)
        b=Admission('2','d'*64,r)
        c=Admission('1','e'*64,r)
        names={record(x,k) for x in (a,b,c)
               for k in ('phase','restart.precommit','restore.precommit','restart.settled','restore.settled')}
        self.assertEqual(len(names),15)
        self.assertEqual({x.parent for x in names},{RECORDS})
        self.assertTrue(str(RECORDS).startswith('/var/lib/'))
        for bad in ('../phase','restore','/tmp/path','restart.settled\n'):
            with self.assertRaises(ValueError):record(a,bad)
