import json
from unittest import TestCase
from pinned_install_plan import GUARD,HOOK,SHELL,KNOWN,plan

class PlanTest(TestCase):
    def sample(self):
        inventory={p:dict(sha256=KNOWN[p],owner=0,group=0,mode=0o755,links=1,kind='regular')
                   for p in (GUARD,HOOK,SHELL)}
        return dict(intended={GUARD:'replace',HOOK:'replace',SHELL:'verify-only'},
                    observed=inventory,inventory=inventory,release='a'*64,
                    new_bytes={GUARD:b'fake guard',HOOK:b'fake hook'})

    def test_deterministic_candidate_and_exact_inverse_pins(self):
        fixture=self.sample()
        result=plan(**fixture)
        fixture['intended']=dict(reversed(list(fixture['intended'].items())))
        self.assertEqual(plan(**fixture),result)
        body=json.loads(result)
        self.assertFalse(body['new_roles'] or body['new_credentials'] or body['data_mutation'])
        for item in body['entries']:
            self.assertEqual(item['rollback'],KNOWN[item['path']])

    def test_wrong_source_pin_or_unapproved_target_rejected(self):
        fixture=self.sample()
        observed={**fixture['observed'],GUARD:{**fixture['observed'][GUARD],'sha256':'b'*64}}
        with self.assertRaises(ValueError):
            plan(**(fixture|dict(observed=observed,inventory=observed)))
        with self.assertRaises(ValueError):
            plan(**(fixture|dict(intended=fixture['intended']|{'/var/lib/application.sqlite3':'replace'})))
