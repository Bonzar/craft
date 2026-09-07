"""Кейсы пробы пути события."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PACKAGE))
sys.path.insert(0, os.path.join(ROOT, "runtime", "pylib"))
sys.path.insert(0, os.path.join(PACKAGE, "scripts", "hooks"))

from decide import REASON, decide  # noqa: E402


EVENT = {
    "harness": "claude",
    "session_id": "sid",
    "call_id": "toolu_1",
    "event": "post-tool",
    "tool": "Bash",
    "input": {"command": "ls"},
    "cwd": "/tmp",
    "state_dir": "/tmp",
    "key": "toolu_1",
}


class TraceProbe(unittest.TestCase):
    def test_отвечает_none_с_причиной(self):
        self.assertEqual(decide(EVENT, [], "/tmp"), {"outcome": "none", "reason": REASON})

    def test_ничего_не_дописывает_в_контекст(self):
        # Дописанный контекст печатается харнесу, и проба, начав его дописывать,
        # перестала бы «ничего не заменять».
        self.assertNotIn("add_context", decide(EVENT, [], "/tmp"))

    def test_не_решает_ни_на_каком_входе(self):
        # Исход не зависит ни от инструмента, ни от входа: проба меряет путь, а не
        # содержимое вызова.
        for tool in ("Bash", "Write", ""):
            event = dict(EVENT, tool=tool, input={"file_path": "/etc/passwd"})
            self.assertEqual(decide(event, [], "/tmp")["outcome"], "none")
