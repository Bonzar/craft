"""Выбор Python для Desktop, в том числе при системном Python 3.9 в PATH."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


def ensure_python():
    if sys.version_info >= (3, 11):
        return
    candidates = []
    for name in ('python3', 'python3.14', 'python3.13', 'python3.12', 'python3.11'):
        found = shutil.which(name)
        if found:
            candidates.append(found)
        for directory in ('/opt/homebrew/bin', '/usr/local/bin'):
            candidates.append(str(Path(directory) / name))
    for candidate in dict.fromkeys(candidates):
        if not Path(candidate).is_file():
            continue
        checked = subprocess.run(
            [candidate, '-c', 'import sys; sys.exit(sys.version_info < (3, 11))'],
            capture_output=True,
        )
        if checked.returncode == 0:
            os.execv(candidate, [candidate, *sys.argv])
    raise SystemExit('Для модулей нужен Python 3.11 или новее; установи его и повтори setup')
