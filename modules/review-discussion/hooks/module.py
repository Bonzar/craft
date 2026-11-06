#!/usr/bin/env python3
"""Prepare and start the shared delivery consumer on session events."""
import os
from pathlib import Path
import re
import sys

MODULE_DIR = Path(__file__).resolve().parents[1]
CORE = MODULE_DIR / '_core'
if not CORE.is_dir():
    CORE = MODULE_DIR.parents[1] / 'core'
sys.path.insert(0, str(CORE))

import jarvis
from jarvis import registry, wrappers


class Module(jarvis.Module):
    def handle(self, event, runtime):
        if os.environ.get('REVIEW_POLL_ENABLED') != '1':
            return jarvis.Silence('review polling is not enabled in this environment')
        library = registry.library_of(runtime.manifest).load()
        try:
            config = library.Config.from_env(os.environ, event.session_id, event.harness)
            library.Client(config.url, os.environ.get('REVIEW_TOKEN', ''))
            library.get_adapter(runtime.module_dir, event.harness, os.environ)
            state = library.start(config, runtime.storage, runtime.module_dir, os.environ)
            return jarvis.Silence('review consumer ' + state)
        except Exception as error:
            reason = str(error) if re.fullmatch(r'[a-z][a-z0-9_]*', str(error)) else type(error).__name__
            library.note(runtime.storage, 'bootstrap_failed', reason=reason)
            return jarvis.Silence('review bootstrap failed: ' + reason)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'bootstrap':
        library = registry.library_of(jarvis.manifest.load(MODULE_DIR)).load()
        raise SystemExit(library.main(MODULE_DIR, sys.argv[2:]))
    raise SystemExit(wrappers.run_hook(__file__, Module))
