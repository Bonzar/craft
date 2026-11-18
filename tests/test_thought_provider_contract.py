import json
import unittest
from pathlib import Path
from unittest.mock import patch
from prototypes.assertion_memory.model import CodexModel,ModelRequest,SubprocessModel,SEMANTIC_BASE_INSTRUCTIONS
from prototypes.assertion_memory.thought_schema import response_schema

class ThoughtProviderContractTest(unittest.TestCase):
    def test_schema_is_passed_to_cli_and_dedicated_base_preserves_auth_config(self):
        def generate(runner,prompt,attempt):
            command=list(runner.command)
            self.assertIn('--ignore-user-config',command)
            self.assertIn('apps',command)
            path=Path(command[command.index('--output-schema')+1])
            self.assertEqual(json.loads(path.read_text()),response_schema('Construct thought',support_count=1))
            base=next(v for v in command if v.startswith('model_instructions_file='))
            self.assertEqual(Path(json.loads(base.split('=',1)[1])).read_text(),SEMANTIC_BASE_INSTRUCTIONS)
            text=json.dumps({'thoughts':[],'needs_detail':None})
            return '\n'.join([json.dumps({'type':'item.completed','item':{'type':'agent_message','text':text}}),json.dumps({'type':'turn.completed'})])
        with patch.object(SubprocessModel,'_generate',generate):
            result=CodexModel(model='gpt-6.1-sol')(ModelRequest('Construct thought',[{'text':'Fixture'}],response_schema=response_schema('Construct thought',support_count=1)))
        self.assertEqual(json.loads(result),{'thoughts':[],'needs_detail':None})
