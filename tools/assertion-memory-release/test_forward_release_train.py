import dataclasses as dc
import sqlite3
import tempfile
import unittest

from forward_release_train import Artifact, Candidate, FixApproval, Ledger, Reject, Witness


def cand(version, char, family='base'):
    return Candidate(family, ('assertion-memory-v' if family == 'base' else 'assertion-memory-visual-v')+version,char*40)


def art(c, ident):
    return Artifact(c,ident,'b'*64)


def witness(stage,a, origin='forward', t=1000, g='a'):
    return Witness(origin,stage,a,g*64,t)


class TestTrain(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.ledger = Ledger(self.db,lambda:1000)
        self.base = witness('prod',art(cand('0.1.36','a'),1))
        self.visual = witness('prod',art(cand('0.1.21','b','visual'),2))
        self.ledger.initialize(self.base)
        self.ledger.initialize(self.visual)
        self.first = cand('1.0.0','c')
        self.l = self.ledger

    def test_open_test_smoke_exact_prod_and_next_major_independent(self):
        self.assertTrue(self.l.reserve(self.first,self.base))
        self.assertFalse(self.l.reserve(self.first))
        self.assertTrue(self.l.reserve(cand('1.0.0','d','visual'),self.visual))
        with self.assertRaises(Reject):
            self.l.reserve(cand('2.0.0','d'),self.base)
        a = art(self.first,4)
        self.l.bind_artifact(a)
        with self.assertRaises(Reject):
            self.l.witness(witness('prod',a))
        self.l.witness(witness('test',a))
        self.l.begin_promotion(witness('test',a))
        with self.assertRaises(Reject):
            self.l.begin_promotion(witness('test',a))
        with self.assertRaises(Reject):
            self.l.witness(witness('prod',art(self.first,5)))
        live = witness('prod',a,g='c')
        self.l.witness(live)
        with self.assertRaises(Reject):
            self.l.reserve(cand('2.0.0','d'),self.base)
        with self.assertRaises(Reject):
            self.l.reserve(cand('2.0.0','d'),dc.replace(live,generation='z'*64))
        self.assertTrue(self.l.reserve(cand('2.0.0','d'),live))

    def test_fix_exact_review_and_broken_predecessor(self):
        self.l.reserve(self.first,self.base)
        a = art(self.first,4)
        self.l.bind_artifact(a)
        f = cand('1.1.0','d')
        approved = FixApproval(a,f,77,88,'c'*64,'open_major_defect_only')
        for bad in (None,dc.replace(approved,fix=cand('1.1.0','f')),
                    dc.replace(approved,classification='adds_unrelated_feature'),
                    dc.replace(approved,predecessor=art(self.first,9)),
                    dc.replace(approved,failing_check_id=0)):
            with self.assertRaises(Reject):
                self.l.reserve(f,fix=bad)
        self.assertTrue(self.l.reserve(f,fix=approved))
        with self.assertRaises(Reject):
            self.l.witness(witness('test',a))
        b = art(f,5)
        self.l.bind_artifact(b)
        with self.assertRaises(Reject):
            self.l.bind_artifact(dc.replace(b,digest='d'*64))
        self.l.witness(witness('test',b))
        ff = cand('1.2.0','e')
        with self.assertRaises(Reject):
            self.l.reserve(ff)

    def test_legacy_no_rollback_no_stale(self):
        for w in (witness('prod',self.base.artifact,origin='rollback'),
                  dc.replace(self.base,observed_at=879), dc.replace(self.base,observed_at=1001)):
            with self.assertRaises(Reject):
                self.l.reserve(self.first,w)
        with self.assertRaises(Reject):
            self.l.reserve(cand('0.1.37','c'),self.base)
        with self.assertRaises(Reject):
            self.l.reserve(Candidate('visual','assertion-memory-v1.0.0','c'*40),self.base)

    def test_single_durable_slot_two_connections(self):
        with tempfile.NamedTemporaryFile() as f:
            x=Ledger(sqlite3.connect(f.name),lambda:1000)
            y=Ledger(sqlite3.connect(f.name),lambda:1000)
            x.initialize(self.base)
            x.reserve(self.first,self.base)
            with self.assertRaises(Reject):
                y.reserve(dc.replace(self.first,revision='e'*40),self.base)
