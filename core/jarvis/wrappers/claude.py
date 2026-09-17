"""Обёртка Claude Code.

Переводит событие Claude в единый формат и единый ответ модуля — в форму
Claude. Логики решений тут нет: обёртка только переводит.

Все факты о харнесе в этом файле помечены источником: «дока» — страница hooks
на code.claude.com, снятая 17.09.2026; «замер» — то, что проверено прогоном.
Чего Claude не умеет, перечислено в UNSUPPORTED_NOTE и возвращается наружу
полем `note`, а не подменяется соседней формой ответа.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from .. import events as ev
from .. import manifest as manifest_reader
from .. import mode as mode_reader
from .. import response as forms
from ..event import Event
from ..module import Delivery, Module, run
from ..registry import modules_dir
from ..response import Response
from ..storage import Storage, default_state_dir

HARNESS = 'claude'

# дока: имена событий Claude и их место в едином каталоге. У одного имени
# харнеса может стоять несколько единых событий: Claude зовёт SessionStart и на
# старте сессии, и после сжатия, а различает их полем `source` (дока).
CLAUDE_EVENTS: dict[str, tuple[str, ...]] = {
    'SessionStart': (ev.SESSION_START, ev.AFTER_COMPACT),
    'UserPromptSubmit': (ev.PROMPT,),
    'PreToolUse': (ev.PRE_TOOL,),
    'PostToolUse': (ev.POST_TOOL,),
    'PostToolUseFailure': (ev.TOOL_ERROR,),
    'Stop': (ev.STOP,),
    'SubagentStop': (ev.SUBAGENT_STOP,),
    'PreCompact': (ev.PRE_COMPACT,),
    'SessionEnd': (ev.SESSION_END,),
    'Notification': (ev.NOTIFICATION,),
}
CLAUDE_BY_EVENT = {
    unified: claude for claude, group in CLAUDE_EVENTS.items() for unified in group
}

# дока: `source` у SessionStart — startup, resume, clear или compact. Замер
# 17.09.2026: после /compact приходит SessionStart с source=compact.
COMPACT_SOURCE = 'compact'


def unified_of(raw: Mapping[str, Any]) -> str:
    """Какое единое событие пришло. Одно имя харнеса — по полю `source`."""
    claude_name = raw.get('hook_event_name')
    group = CLAUDE_EVENTS.get(claude_name)
    if group is None:
        raise ValueError(
            f'событие Claude {claude_name!r} не заведено в едином каталоге; '
            f'есть {sorted(CLAUDE_EVENTS)}'
        )
    if claude_name == 'SessionStart':
        return ev.AFTER_COMPACT if raw.get('source') == COMPACT_SOURCE else ev.SESSION_START
    return group[0]


# дока: инструмент вопроса с кнопками у Claude Code есть, поэтому форма
# «вопрос человеку» переводится в поручение вызвать именно его.
QUESTION_TOOL = 'AskUserQuestion'

# дока, «Decision control»: какие формы единого ответа событие Claude принимает.
# Молчание принимает любое событие и в таблице не перечисляется.
SUPPORTED: dict[str, frozenset[str]] = {
    ev.SESSION_START: frozenset({forms.CONTEXT, forms.QUESTION}),
    ev.AFTER_COMPACT: frozenset({forms.CONTEXT, forms.QUESTION}),
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
    ev.AFTER_COMPACT: 'Claude после сжатия принимает только контекст: это тот же SessionStart (дока)',
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
    return Event(
        event=unified_of(raw),
        session_id=str(raw.get('session_id') or ''),
        cwd=str(raw.get('cwd') or ''),
        harness=HARNESS,
        raw=raw,
        tool_name=raw.get('tool_name'),
        tool_input=raw.get('tool_input'),
        tool_result=raw.get('tool_response'),
        error=raw.get('error'),
        prompt_text=raw.get('prompt'),
        human_answer=human_answer(raw),
    )


# дока: личный конфиг режимов лежит в каталоге пользователя. Путь общий для
# всех харнесов и потому задан в `jarvis.mode`.
PERSONAL_CONFIG = mode_reader.PERSONAL_CONFIG


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


def run_hook(entry_file: str, module_class: type[Module], argv: list[str] | None = None,
             stdin=None, stdout=None, stderr=None, env: Mapping[str, str] | None = None) -> int:
    """Провести модуль по одному событию Claude и напечатать ответ.

    Это и есть обёртка: харнес запускает файл модуля, файл зовёт сюда. Путь к
    себе файл знает, поэтому каталог модулей, ядро, шапка и соседи находятся от
    него, без аргумента «корень установки» и без записи установщика.
    """
    parser = argparse.ArgumentParser(description='Обёртка Claude вокруг одного модуля Джарвиса')
    parser.add_argument(
        '--event',
        required=True,
        action='append',
        dest='events',
        help='единое имя события, на которое стоит строка; повторяется, '
             'когда харнес приносит их одним своим событием',
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    stdin = sys.stdin if stdin is None else stdin
    raw = json.loads(stdin.read())
    event = to_event(raw)
    if event.event not in args.events:
        claude_name = raw.get('hook_event_name')
        siblings = CLAUDE_EVENTS.get(claude_name, ())
        unknown = [name for name in args.events if name not in siblings]
        if unknown:
            raise ValueError(
                f'строка зарегистрирована на «{", ".join(args.events)}», а пришло '
                f'«{event.event}»: настройки харнеса разошлись с установкой'
            )
        # Одно событие харнеса приносит несколько единых: строка соседнего
        # события молчит, а не падает. Следа нет — модуль на этом событии не
        # стоит, и его ход не начинался.
        return 0

    module_dir = Path(entry_file).resolve().parents[1]
    manifest = manifest_reader.load(module_dir)
    outcome = run(
        module_class(),
        manifest,
        event,
        Storage(default_state_dir(), event.session_id),
        translate=lambda unified, response: translate(unified, response, slug=manifest.slug),
        module_dir=module_dir,
        personal_config=Path(PERSONAL_CONFIG).expanduser(),
        source_config=modules_dir(module_dir) / mode_reader.SOURCE_CONFIG_NAME,
        env=env,
    )
    return emit(outcome.delivery, stdout=stdout, stderr=stderr)
