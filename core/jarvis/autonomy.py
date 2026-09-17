"""Признак «человека нет».

Одна переменная окружения, которую всегда ставит автономный прогон.
Ни режима в шапке, ни второй переменной у этого признака нет.
"""

import os
from typing import Mapping

ENV_AUTONOMOUS = 'JARVIS_AUTONOMOUS'
AUTONOMOUS_VALUE = '1'


def is_autonomous(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(ENV_AUTONOMOUS) == AUTONOMOUS_VALUE
