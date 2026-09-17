"""Ядро общего кода, которое есть у каждого модуля.

Хранилище, режим, признак автономии, след, сверка подтверждения, поиск модуля
по slug. Ядро лежит папкой `_core` в каталоге модулей, рядом с самими модулями:
модуль находит его от собственного файла, установщик для этого не нужен.
"""

from . import confirm, events, manifest, mode, registry, response, storage, trace
from .autonomy import ENV_AUTONOMOUS, is_autonomous
from .event import Event
from .module import Delivery, Module, Outcome, Runtime, run
from .response import (
    Allow,
    Ask,
    Block,
    Context,
    Deny,
    Question,
    Response,
    Silence,
    UpdatedInput,
)
from .storage import Storage
from .trace import Trace

__all__ = [
    'Allow',
    'Ask',
    'Block',
    'Context',
    'Delivery',
    'Deny',
    'ENV_AUTONOMOUS',
    'Event',
    'Module',
    'Outcome',
    'Question',
    'Response',
    'Runtime',
    'Silence',
    'Storage',
    'Trace',
    'UpdatedInput',
    'confirm',
    'events',
    'is_autonomous',
    'manifest',
    'mode',
    'registry',
    'response',
    'run',
    'storage',
    'trace',
]
