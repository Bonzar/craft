import unittest
from render_test_graph_direct import render,PUBLIC,DIRECT,ROOT

class GraphFlag(unittest.TestCase):
    def test_preserve_and_append_exact_origins(self):
        unit='ExecStart=%h/current/venv/bin/python -B -m apps.graph_viewer.server --source %h/state/fictional.sqlite3 --password-file %h/state/graph-password --port 8891 --synthetic-fixture'
        release=f"{ROOT}/releases/{'a'*40}-{'b'*64}"
        expected=f'[Service]\nExecStart=\n{unit} --public-origin {PUBLIC} --test-direct-events-origin {DIRECT}\n'
        self.assertEqual(render(unit,release,PUBLIC,DIRECT),expected)
        for changed in [unit.replace('8891','8901'),unit+' --database-clear-lane test',
                        unit.replace('/fictional.sqlite3','/prod.sqlite3'),unit+' --public-origin '+PUBLIC]:
            with self.assertRaises(ValueError):
                render(changed,release,PUBLIC,DIRECT)
        with self.assertRaises(ValueError):
            render(unit,release,'https://wrong.invalid',DIRECT)
