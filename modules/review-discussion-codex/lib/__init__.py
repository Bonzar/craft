"""Deliver text to the configured existing Codex thread; never resume or fork."""
import os
import re
import shutil
import subprocess
import uuid


class Adapter:
    def __init__(self, env=None):
        self.env = dict(os.environ if env is None else env)
        self.executable = self.env.get('REVIEW_CODEX_EXECUTABLE') or shutil.which(
            'codex', path=self.env.get('PATH'))
        if not self.executable:
            raise RuntimeError('codex_executable_missing')

    def send(self, target, payload):
        target = str(uuid.UUID(target))
        # The Site credential belongs only to the HTTP consumer, not to Codex.
        environment = {key: value for key, value in self.env.items()
                       if key != 'REVIEW_TOKEN'}
        try:
            done = subprocess.run(
                [self.executable, 'queue', '--thread', target, '--message', payload],
                env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, encoding='utf-8', timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('codex_queue_unconfirmed') from None
        if done.returncode:
            raise RuntimeError('codex_queue_failed')
        found = re.fullmatch(r'Queued message ([A-Za-z0-9_-]+) for thread '
                             + re.escape(target) + r'\.\s*', done.stdout)
        if not found:
            raise RuntimeError('codex_queue_receipt_invalid')
        return found.group(1)
