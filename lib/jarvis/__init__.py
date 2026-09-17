"""Ядро общего кода, которое есть у каждого модуля.

Хранилище, режим, признак автономии, след, сверка подтверждения, поиск модуля
по slug. Установщик собирает библиотеку один раз, копий нет.
"""

from . import confirm, events, install, manifest, mode, registry, response, storage, trace
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
    'install',
    'is_autonomous',
    'manifest',
    'mode',
    'registry',
    'response',
    'run',
    'storage',
    'trace',
]
