"""Доставляет отрендеренные правила worktree в Desktop с внешним профилем."""

import os
from pathlib import Path

from jarvis import registry


def provide(event, storage) -> str:
    module = Path(__file__).resolve().parents[1]
    settings = registry.modules_dir(module).parent.parent
    active_home = Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser().resolve()
    if active_home == settings.resolve():
        # CLI уже читает этот AGENTS.md как инструкции своего CODEX_HOME.
        return ''
    return (settings / 'AGENTS.md').read_text(encoding='utf-8')
