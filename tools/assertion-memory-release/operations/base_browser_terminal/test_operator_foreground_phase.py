from unittest import TestCase
from configured_terminal_state import initial,admit
from operator_foreground_phase import planned_phase,ApprovedPair
from root_phase_binding import document
from terminal_lifecycle import TerminalRequest
from transition_witness import Admission,Release

old=Release('a'*40,'b'*64,'c'*64,'legacy')
new=Release('d'*40,'e'*64,'f'*64,'sidecar')
signed=Admission('55','9'*64,new)
pair=ApprovedPair(signed,old)
state=admit(initial(trusted_prior=old),verified_admission=signed)
exact_journal=(signed.run,signed.nonce,old,new)
def observing(selected, journal=exact_journal):
    def observe(req):
        if req.phase!=selected:
            raise ValueError('wrong physical')
        return (new if selected=='restart' else old),journal
    return observe

class Foreground(TestCase):
    def test_candidate_and_predecessor_phases_are_monotonic(self):
        forward=planned_phase(pair,state=state,prior_phase=None,native_observer=observing('restart'))
        back=planned_phase(pair,state=state,prior_phase=forward,native_observer=observing('restore'))
        self.assertEqual(forward,document(TerminalRequest(signed,old,'restart')))
        self.assertEqual(back,document(TerminalRequest(signed,old,'restore')))
        self.assertEqual(back,planned_phase(pair,state=state,prior_phase=back,native_observer=observing('restore')))
        for previous,selected in ((None,'restore'),(back,'restart'),(b'untrusted','restart')):
            with self.assertRaises(ValueError):
                planned_phase(pair,state=state,prior_phase=previous,native_observer=observing(selected))

    def test_no_foreign_replay_missing_journal_or_ambiguous_state(self):
        for observer in (observing('restart',None),observing('restore',('wrong',*exact_journal[1:])),
                         lambda r: (new if r.phase=='restart' else old,exact_journal)):
            with self.assertRaises(ValueError):
                planned_phase(pair,state=state,prior_phase=None,native_observer=observer)
        with self.assertRaises(ValueError):
            planned_phase(pair,state=initial(trusted_prior=old),prior_phase=None,native_observer=observing('restart'))
