"""Normal Codex generation is unbounded; explicit custom commands keep deadlines."""
import json
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.model import CodexModel, ModelError, ModelRequest, SubprocessModel
from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.storage import MemoryStore


class CodexGenerationTimeoutTest(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name);scratch=self.root/'cwd';scratch.mkdir(mode=0o700)
        patcher=patch('prototypes.assertion_memory.model._semantic_working_directory',return_value=str(scratch))
        patcher.start();self.addCleanup(patcher.stop)
        self.request=ModelRequest('Local timeout fixture',[])

    def binary(self,body):
        path=self.root/'local-codex-fixture'
        path.write_text('#!'+sys.executable+'\nimport sys,json,time\nsys.stdin.read()\n'+body+'\n')
        path.chmod(0o700)
        return str(path)

    def test_live_isolated_codex_child_outlasts_configured_wall_timeout(self):
        binary=self.binary("time.sleep(.08)\nprint(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'finished after the old deadline'}}))\nprint(json.dumps({'type':'turn.completed'}))")
        config=ServiceConfig(str(self.root/'fixture.sqlite3'),'codex',model='gpt-6.1-sol',timeout=.005)
        self.assertIsNone(config.timeout)
        serialized=json.loads(json.dumps(asdict(config)))
        self.assertIsNone(serialized['timeout'])
        self.assertIsNone(ServiceConfig(**serialized).timeout)
        with MemoryStore(config.db) as store,patch('prototypes.assertion_memory.mcp_server.CodexModel',
                side_effect=lambda **options:CodexModel(binary=binary,**options)):
            model,_,_=config.components(store)
            self.assertIsNone(model.timeout)
            self.assertEqual(model(self.request),'finished after the old deadline')

    def test_custom_commands_keep_explicit_timeout_and_none_is_supported(self):
        command=(sys.executable,'-c',"import time;time.sleep(.08);print('done')")
        for constructor in (SubprocessModel,CodexModel):
            with self.subTest(constructor=constructor.__name__):
                with self.assertRaisesRegex(ModelError,'timed out'):
                    constructor(command,timeout=.005)(self.request)
        self.assertEqual(SubprocessModel(command,timeout=None)(self.request),'done')
        explicit=self.binary("time.sleep(.08)\nprint(json.dumps({'type':'turn.completed'}))")
        with self.assertRaisesRegex(ModelError,'timed out'):
            CodexModel(binary=explicit,model='gpt-6.1-sol',timeout=.005)(self.request)
        configured=ServiceConfig(str(self.root/'custom.sqlite3'),'subprocess',command=command,timeout=.005)
        self.assertEqual(configured.timeout,.005)
        local=ServiceConfig(str(self.root/'local.sqlite3'),'local',endpoint='http://127.0.0.1:1',model='fixture',timeout=.005)
        self.assertEqual(local.timeout,.005)

    def test_isolated_codex_still_reports_real_exit_and_invalid_output(self):
        for body,message in (("time.sleep(.02)\nsys.exit(7)",'exited with code 7'),
            ("time.sleep(.02)\nprint('not a Codex event stream')",'invalid event stream'),
            ("print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'unfinished'}}))",'did not return a completed')):
            with self.subTest(failure=message):
                with self.assertRaisesRegex(ModelError,message):
                    CodexModel(binary=self.binary(body),model='gpt-6.1-sol')(self.request)


if __name__=='__main__':unittest.main()
