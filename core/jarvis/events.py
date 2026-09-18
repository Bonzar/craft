"""Каталог единых имён событий.

Имена событий в коде единые и английские: русские названия живут в Craft.
Событие попадает в каталог, когда на нём встаёт первый модуль, — здесь те
десять, которые этап 2 закрыл обёрткой Claude, и «после сжатия», на котором
стоит база стартового контекста.

«После сжатия» — отдельное имя, а не оттенок старта сессии: у Claude оно
приезжает тем же событием харнеса с другим полем `source`, у Codex — своим
событием. Единый формат прячет эту разницу: модуль пишет `after-compact` и не
знает, чем его харнес это событие называет.
"""

SESSION_START = 'session-start'
AFTER_COMPACT = 'after-compact'
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
    AFTER_COMPACT,
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
