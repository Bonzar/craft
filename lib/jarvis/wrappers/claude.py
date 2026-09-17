"""Обёртка Claude Code.

Переводит событие Claude в единый формат и единый ответ модуля — в форму
Claude. Логики решений тут нет: обёртка только переводит.

Все факты о харнесе в этом файле помечены источником: «дока» — страница hooks
на code.claude.com, снятая 17.09.2026; «замер» — то, что проверено прогоном.
Чего Claude не умеет, перечислено в UNSUPPORTED_NOTE и возвращается наружу
полем `note`, а не подменяется соседней формой ответа.
"""

import json
import sys
from typing import Any, Mapping

from .. import events as ev
from .. import response as forms
from ..event import Event
from ..module import Delivery
from ..response import Response

HARNESS = 'claude'

# дока: имена событий Claude и их место в едином каталоге.
EVENT_BY_CLAUDE = {
    'SessionStart': ev.SESSION_START,
    'UserPromptSubmit': ev.PROMPT,
    'PreToolUse': ev.PRE_TOOL,
    'PostToolUse': ev.POST_TOOL,
    'PostToolUseFailure': ev.TOOL_ERROR,
    'Stop': ev.STOP,
    'SubagentStop': ev.SUBAGENT_STOP,
    'PreCompact': ev.PRE_COMPACT,
    'SessionEnd': ev.SESSION_END,
    'Notification': ev.NOTIFICATION,
}
CLAUDE_BY_EVENT = {unified: claude for claude, unified in EVENT_BY_CLAUDE.items()}

# дока: инструмент вопроса с кнопками у Claude Code есть, поэтому форма
# «вопрос человеку» переводится в поручение вызвать именно его.
QUESTION_TOOL = 'AskUserQuestion'

# дока, «Decision control»: какие формы единого ответа событие Claude принимает.
# Молчание принимает любое событие и в таблице не перечисляется.
SUPPORTED: dict[str, frozenset[str]] = {
    ev.SESSION_START: frozenset({forms.CONTEXT, forms.QUESTION}),
    ev.PROMPT: frozenset({forms.CONTEXT, forms.QUESTION, forms.BLOCK}),
    ev.PRE_TOOL: frozenset(
        {forms.CONTEXT, forms.QUESTION, forms.ALLOW, forms.ASK, forms.DENY, forms.UPDATED_INPUT}
    ),
    ev.POST_TOOL: frozenset({forms.CONTEXT, forms.QUESTION, forms.BLOCK}),
    ev.TOOL_ERROR: frozenset({forms.CONTEXT, forms.QUESTION}),
    ev.STOP: frozenset({forms.CONTEXT, forms.QUESTION, forms.BLOCK}),
    ev.SUBAGENT_STOP: frozenset({forms.CONTEXT, forms.QUESTION, forms.BLOCK}),
    ev.PRE_COMPACT: frozenset({forms.BLOCK}),
    ev.SESSION_END: frozenset(),
    ev.NOTIFICATION: frozenset(),
}

# Чего харнес не умеет — список самой обёртки, дословно для отчёта в чат.
UNSUPPORTED_NOTE = {
    ev.SESSION_START: 'Claude на старте сессии принимает только контекст: решения там нет (дока)',
    ev.PROMPT: 'Claude на реплике принимает контекст и блок; изменённой реплики у него нет (дока)',
    ev.PRE_TOOL: 'Claude перед вызовом блок не принимает — запрет выражается формой «запретить» (дока)',
    ev.POST_TOOL: 'Claude после вызова принимает контекст и блок: вызов уже прошёл (дока)',
    ev.TOOL_ERROR: 'Claude после ошибки вызова принимает только контекст — блока там нет (дока)',
    ev.STOP: 'Claude на остановке хода принимает контекст и блок (дока)',
    ev.SUBAGENT_STOP: 'Claude на остановке подагента принимает контекст и блок (дока)',
    ev.PRE_COMPACT: 'Claude перед сжатием принимает только блок: контекста туда не положить (дока)',
    ev.SESSION_END: 'Claude на конце сессии вывод хука выбрасывает целиком (дока)',
    ev.NOTIFICATION: 'Claude на уведомлении вывод хука выбрасывает целиком (дока)',
}


def question_instruction(question: forms.Question, slug: str) -> str:
    """Поручение модели: спросить человека инструментом вопроса харнеса."""
    options = '; '.join(question.options) if question.options else 'вариантов модуль не дал'
    return (
        f'Модулю «{slug}» нужен ответ человека, сам он спросить не может. '
        f'Задай вопрос инструментом {QUESTION_TOOL}. '
        f'Вопрос: {question.text} Варианты: {options}'
    )


def human_answer(raw: Mapping[str, Any]) -> Any:
    """Ответ человека на вопрос — из результата вызова инструмента вопроса.

    замер не проведён: формы `tool_response` у AskUserQuestion дока не
    показывает, поэтому берём поле `answers`, а когда его нет — результат
    целиком, чтобы ответ не потерялся молча.
    """
    if raw.get('tool_name') != QUESTION_TOOL:
        return None
    result = raw.get('tool_response')
    if isinstance(result, Mapping) and 'answers' in result:
        return result['answers']
    return result


def to_event(raw: Mapping[str, Any]) -> Event:
    """Событие Claude — в единый формат."""
    claude_name = raw.get('hook_event_name')
    unified = EVENT_BY_CLAUDE.get(claude_name)
    if unified is None:
        raise ValueError(
            f'событие Claude {claude_name!r} не заведено в едином каталоге; '
            f'есть {sorted(EVENT_BY_CLAUDE)}'
        )
    return Event(
        event=unified,
        session_id=str(raw.get('session_id') or ''),
        cwd=str(raw.get('cwd') or ''),
        harness=HARNESS,
        raw=raw,
        tool_name=raw.get('tool_name'),
        tool_input=raw.get('tool_input'),
        tool_result=raw.get('tool_response'),
        prompt_text=raw.get('prompt'),
        human_answer=human_answer(raw),
    )


def _hook_specific(unified: str, fields: dict) -> dict:
    return {'hookSpecificOutput': {'hookEventName': CLAUDE_BY_EVENT[unified], **fields}}


def _context_payload(unified: str, text: str) -> dict:
    return _hook_specific(unified, {'additionalContext': text})


def translate(unified: str, response: Response, slug: str = '') -> Delivery:
    """Единый ответ — в форму Claude."""
    if unified not in SUPPORTED:
        raise ValueError(f'события {unified!r} нет в едином каталоге')
    if response.kind == forms.SILENCE:
        return Delivery()
    if response.kind not in SUPPORTED[unified]:
        return Delivery(supported=False, note=UNSUPPORTED_NOTE[unified])

    if response.kind == forms.CONTEXT:
        return Delivery(payload=_context_payload(unified, response.text))
    if response.kind == forms.QUESTION:
        return Delivery(payload=_context_payload(unified, question_instruction(response, slug)))
    if response.kind == forms.BLOCK:
        # дока: block на этих событиях — верхнеуровневые decision и reason.
        return Delivery(payload={'decision': 'block', 'reason': response.reason})
    if response.kind == forms.ALLOW:
        return Delivery(
            payload=_hook_specific(
                unified,
                {'permissionDecision': 'allow', 'permissionDecisionReason': response.reason},
            )
        )
    if response.kind == forms.DENY:
        return Delivery(
            payload=_hook_specific(
                unified,
                {'permissionDecision': 'deny', 'permissionDecisionReason': response.reason},
            )
        )
    if response.kind == forms.ASK:
        # Фраза подтверждения идёт человеку той же причиной: своего поля под
        # неё у Claude нет (дока).
        reason = f'{response.reason} Подтверди дословно: {response.phrase}'
        return Delivery(
            payload=_hook_specific(
                unified,
                {'permissionDecision': 'ask', 'permissionDecisionReason': reason},
            )
        )
    if response.kind == forms.UPDATED_INPUT:
        return Delivery(payload=_hook_specific(unified, {'updatedInput': dict(response.tool_input)}))

    raise ValueError(f'форма ответа {response.kind!r} обёртке Claude неизвестна')


def emit(delivery: Delivery, stdout=None, stderr=None) -> int:
    """Напечатать ответ в форме Claude и вернуть код выхода."""
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    if delivery.note:
        stderr.write(delivery.note + '\n')
    if delivery.payload is not None:
        stdout.write(json.dumps(delivery.payload, ensure_ascii=False))
    return delivery.exit_code
