import unittest,json
from prototypes.assertion_memory.model import _context_error

class ModelContextCapacityTest(unittest.TestCase):
    def test_provider_error_is_distinct_from_quoted_user_content(self):
        error=json.dumps({'type':'turn.failed','error':{'code':'context_length_exceeded'}})
        content=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':error}})
        self.assertTrue(_context_error(error));self.assertFalse(_context_error(content))
        self.assertFalse(_context_error(json.dumps({'type':'error','error':{'code':'authentication_failed'}})))
