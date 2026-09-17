"""Тесты без харнеса: стандартный unittest, ничего сверх стандартной библиотеки.

Запуск из корня репозитория:

    python3 -m unittest discover -s tests -t .
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULES_DIR = REPO_ROOT / 'modules'
CORE_SOURCE = REPO_ROOT / 'core'
INSTALLER = REPO_ROOT / 'tools' / 'jarvis-install'

if str(CORE_SOURCE) not in sys.path:
    sys.path.insert(0, str(CORE_SOURCE))
