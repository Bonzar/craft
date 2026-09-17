"""Тесты без харнеса: стандартный unittest, ничего сверх стандартной библиотеки.

Запуск из корня репозитория:

    python3 -m unittest discover -s tests -t .
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULES_DIR = REPO_ROOT / 'modules'
CORE_DIR = MODULES_DIR / '_core'
INSTALLER = REPO_ROOT / 'tools' / 'jarvis-install'

if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))
