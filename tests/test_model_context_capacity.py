import unittest,json,sys
from prototypes.assertion_memory.model import (ModelRequest, SubprocessModel, _context_error,
                                               format_prompt, processing_cancellation)

class ModelContextCapacityTest(unittest.TestCase):
    def test_provider_error_is_distinct_from_quoted_user_content(self):
        error=json.dumps({'type':'turn.failed','error':{'code':'context_length_exceeded'}})
        content=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':error}})
        self.assertTrue(_context_error(error));self.assertFalse(_context_error(content))
        self.assertFalse(_context_error(json.dumps({'type':'error','error':{'code':'authentication_failed'}})))


    def test_owned_model_receives_the_complete_prompt_after_slow_stdin_read(self):
        reader = ('import sys,time; time.sleep(.2); '
                  'print(len(sys.stdin.buffer.read()))')
        provider = SubprocessModel((sys.executable, '-c', reader), timeout=2)
        request = ModelRequest('Read the prompt', [{'text': 'x' * 300_000}])
        with processing_cancellation(lambda: False):
            answer = provider(request)
        self.assertEqual(int(answer), len(format_prompt(request).encode('utf-8')))
