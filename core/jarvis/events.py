"""Каталог единых имён событий.

Имена событий в коде единые и английские: русские названия живут в Craft.
Событие попадает в каталог, когда на нём встаёт первый модуль, — здесь ровно
те десять, которые этап 2 закрывает обёрткой Claude.
"""

SESSION_START = 'session-start'
PROMPT = 'prompt'
PRE_TOOL = 'pre-tool'
POST_TOOL = 'post-tool'
TOOL_ERROR = 'tool-error'
STOP = 'stop'
SUBAGENT_STOP = 'subagent-stop'
PRE_COMPACT = 'pre-compact'
SESSION_END = 'session-end'
NOTIFICATION = 'notification'

ALL = (
    SESSION_START,
    PROMPT,
    PRE_TOOL,
    POST_TOOL,
    TOOL_ERROR,
    STOP,
    SUBAGENT_STOP,
    PRE_COMPACT,
    SESSION_END,
    NOTIFICATION,
)
