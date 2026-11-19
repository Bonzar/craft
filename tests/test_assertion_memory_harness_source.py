"""Typed non-user provenance never becomes a missing-user delivery gap."""
import unittest
from . import test_assertion_memory_capture as fixtures


class HarnessSourceTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CaptureTest(methodName='runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)

    def injection(self,kind,provenance):
        self.f.append({'type':'response_item','payload':{'type':'message','role':'user',
            'content':[{'type':'input_text','text':'Generated context; never user words.'}],
            'internal_chat_message_metadata_passthrough':{'turn_id':'one','content_item_kinds':[kind],
                'content_item_metadata':[{'provenance':provenance}]}}})

    def test_typed_harness_context_kinds_are_excluded_without_a_name_whitelist(self):
        f=self.f;f.turn('Actual user words',trigger='user')
        for kind in ('additional_content.codex_apps_open_page','additional_content.codex_apps_selected_page_part_1_of_1','user.goal','unknown'):
            self.injection(kind,{'type':'harness','hook':kind=='unknown'})
        f.assistant('Actual visible answer')
        f.capture.register(f.store,f.raw);result=f.poll()
        self.assertEqual([m['arguments']['text'] for m in f.messages()],['Actual user words','Actual visible answer'])
        self.assertEqual(result['gaps'],[])
        self.assertEqual(f.store.session('physical')['exclusions']['harness_service_context'],4)

    def test_user_or_missing_provenance_is_never_reclassified_as_harness(self):
        f=self.f;f.turn('Actual user words',trigger='user')
        self.injection('unknown',{'type':'user'})
        self.injection('additional_content.codex_apps_open_page',{})
        f.assistant('Actual visible answer')
        f.capture.register(f.store,f.raw);result=f.poll()
        self.assertEqual(result['gaps'],['unsupported_user_schema','unsupported_user_schema'])
        self.assertEqual([m['arguments']['text'] for m in f.messages()],['Actual user words','Actual visible answer'])


if __name__=='__main__':unittest.main()
