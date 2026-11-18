"""Internal semantic jobs exclude unrelated project guidance without weakening permissions."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.model import CodexModel, ModelRequest, format_prompt


class SemanticCLIContextTest(unittest.TestCase):
    def test_isolated_command_excludes_project_docs_and_automatic_skill_instructions(self):
        model = CodexModel(model='configured-model')
        overrides = [model.command[index+1] for index,value in enumerate(model.command) if value == '-c']
        self.assertIn('project_doc_max_bytes=0', overrides)
        self.assertIn('skills.include_instructions=false', overrides)
        self.assertIn('--ignore-user-config', model.command)
        self.assertEqual(model.command[model.command.index('--sandbox')+1], 'read-only')
        for feature in ('hooks','shell_tool','plugins'):
            self.assertIn(('--disable',feature), tuple(zip(model.command,model.command[1:])))
        request = ModelRequest('Generate memory answer', ({'text':'synthetic material'},),
                               instructions='Keep every semantic qualification.')
        with patch('prototypes.assertion_memory.model.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '\n'.join(json.dumps(event) for event in (
                {'type':'item.completed','item':{'type':'agent_message','text':'fixture'}},
                {'type':'turn.completed','usage':{}},
            ))
            def check_scratch(command, **kwargs):
                directory = Path(command[command.index('-C')+1])
                self.assertTrue(directory.is_dir())
                self.assertFalse((directory/'AGENTS.md').exists())
                return run.return_value
            run.side_effect = check_scratch
            self.assertEqual(model(request),'fixture')
            command = run.call_args.args[0]
            scratch = Path(command[command.index('-C')+1])
            self.assertTrue(str(scratch.name).startswith('assertion-memory-model-'))
            self.assertFalse((scratch/'AGENTS.md').exists())
            self.assertEqual(run.call_args.kwargs['input'],format_prompt(request))
            self.assertEqual(model(request),'fixture')
            second=run.call_args.args[0]
            self.assertEqual(second[second.index('-C')+1],str(scratch))
            self.assertEqual(run.call_count,2)
            self.assertIn('--ephemeral',second)

    def test_explicit_custom_command_is_not_rewritten(self):
        model = CodexModel(command=('custom-provider','--custom-argument'))
        self.assertEqual(model.command,('custom-provider','--custom-argument'))
