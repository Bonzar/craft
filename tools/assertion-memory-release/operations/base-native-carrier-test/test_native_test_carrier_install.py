from contextlib import contextmanager
from dataclasses import replace
from unittest import TestCase

from native_test_carrier_install import (
    Evidence, LEGACY_UNIT, Mode, existing_public_origin, transplant,
)


class Adapter:
    def __init__(self):
        self.expected = Evidence('test','a'*40,'b'*64,LEGACY_UNIT,8893,8891,
                                 existing_public_origin(),(1,10),(1,20))
        self.mode = Mode.LEGACY
        self.calls = []
        self.bad_smoke = self.drift = False
        self.locked = False

    @contextmanager
    def exclusive(self):
        assert not self.locked
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def admit(self, permit, *, inverse):
        assert self.locked
        if permit is not (self.undo if inverse else self.forward):
            raise ValueError('wrong_permit')
        return self.expected

    forward, undo = object(), object()

    def observe(self):
        assert self.locked
        return (replace(self.expected,archive='c'*64) if self.drift else self.expected,self.mode)

    def check_shadow(self, evidence):
        assert self.locked and self.mode == Mode.LEGACY and evidence == self.expected
        self.calls.append('shadow')

    def retire(self, mode, evidence):
        assert self.locked and self.mode == mode and evidence == self.expected
        self.calls.append(('retire',mode))
        self.mode = Mode.DARK

    def start(self, mode, evidence):
        assert self.locked and self.mode == Mode.DARK and evidence == self.expected
        self.calls.append(('start',mode))
        self.mode = mode

    def smoke(self, mode, evidence):
        self.calls.append(('smoke',mode))
        if mode is Mode.NATIVE and self.bad_smoke:
            raise ValueError('candidate_unhealthy')


class Cutover(TestCase):
    def test_exact_forward_inverse_no_dual_owner(self):
        actor=Adapter()
        self.assertIs(transplant(actor,actor.forward),Mode.NATIVE)
        self.assertEqual(actor.calls[:4], ['shadow',('retire',Mode.LEGACY),
                            ('start',Mode.NATIVE),('smoke',Mode.NATIVE)])
        self.assertIs(transplant(actor,actor.undo,inverse=True),Mode.LEGACY)
        self.assertEqual(actor.calls[-3:],[('retire',Mode.NATIVE),('start',Mode.LEGACY),('smoke',Mode.LEGACY)])

    def test_failed_smoke_restores_old_origin_and_scope(self):
        actor=Adapter(); initial=actor.expected; actor.bad_smoke=True
        with self.assertRaisesRegex(ValueError,'candidate_unhealthy'):
            transplant(actor,actor.forward)
        self.assertIs(actor.mode,Mode.LEGACY)
        self.assertEqual(actor.expected,initial)
        self.assertEqual(actor.calls[-3:],[('retire',Mode.NATIVE),('start',Mode.LEGACY),('smoke',Mode.LEGACY)])

    def test_wrong_lane_or_stale_or_wrong_permit_never_switches(self):
        for kind in ('lane','drift','permit'):
            with self.subTest(kind=kind):
                actor=Adapter()
                if kind=='lane':
                    actor.expected=replace(actor.expected,stage='prod')
                actor.drift=kind=='drift'
                with self.assertRaises(ValueError):
                    transplant(actor,object() if kind=='permit' else actor.forward)
                self.assertIs(actor.mode,Mode.LEGACY)
                self.assertEqual(actor.calls,[])
