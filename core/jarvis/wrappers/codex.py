"""Обёртка Codex: перевод событий и ответов без логики модулей.

Протокол: https://learn.chatgpt.com/docs/hooks и схема app-server.
Замер 04.11.2026, codex-cli 0.166.0-alpha.6: PreToolUse deny предотвращает
выполнение, PostToolUse приходит после результата, Stop block продолжает тот
же turn_id без повторного UserPromptSubmit. Проверено локальным модельным
stub: запрещённый файл не создан, контрольный разрешённый создан.

Показ ответа модели подменить нельзя. Нативный PreToolUse ask не поддержан:
Codex считает его ошибкой хука и продолжает вызов, поэтому его не отправляем.
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

HARNESS = 'codex'

# замер: после сжатия Codex перезапускает тот же хук SessionStart и ставит во
# вход `"source": "compact"` — ровно как Claude. События `PostCompact` у него
# тоже есть, и хук на нём исполняется, но его stdout отбрасывается, так что
# нести туда стартовый контекст нечем и незачем.
CODEX_EVENTS: dict[str, tuple[str, ...]] = {
    'SessionStart': (ev.SESSION_START, ev.AFTER_COMPACT),
    'PermissionRequest': (ev.PERMISSION_REQUEST,),
    'SubagentStart': (ev.SUBAGENT_START,),
    'UserPromptSubmit': (ev.PROMPT,),
    'PreToolUse': (ev.PRE_TOOL,),
    'PostToolUse': (ev.POST_TOOL,),
    'Stop': (ev.STOP,),
}
CODEX_BY_EVENT = {
    unified: native for native, group in CODEX_EVENTS.items() for unified in group
}

# Какие события Codex принимают текст в ход. По ним установщик решает, писать
# ли в строку хука ключ снятия потолка: где текста не ждут, Codex на ключ
# ругается предупреждением. `SessionStart` несёт оба наших события — и старт
# сессии, и «после сжатия».
#
# замер 18.09.2026 (codex-cli 0.155.0): дописанный руками
# `additionalContextLimit = 0` на `SubagentStart` Codex принимает молча и
# показывает в `hooks/list` как 0, а на `PermissionRequest` отвечает
# «ignoring additionalContextLimit … this event cannot emit additionalContext»
# — ровно как на `PostCompact`.
CONTEXT_EVENTS = frozenset({'SessionStart', 'SubagentStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse'})

# Личный конфиг режимов общий для харнесов.
PERSONAL_CONFIG = mode_reader.PERSONAL_CONFIG

# замер: `source` у SessionStart — startup, resume или compact. После сжатия
# Codex перезапускает хук с source=compact, и его stdout доходит до модели
# свежим, а не из кэша старта.
COMPACT_SOURCE = 'compact'

SUPPORTED: dict[str, frozenset[str]] = {
    ev.SESSION_START: frozenset({forms.CONTEXT}),
    ev.AFTER_COMPACT: frozenset({forms.CONTEXT}),
    # замер 18.09.2026: схемы ответов у Codex один в один как у Claude —
    # `decision` с `behavior` allow или deny на запросе разрешения и
    # `additionalContext` на старте подагента. Поля `updatedInput`,
    # `updatedPermissions` и `interrupt` схема принимает, но помечает
    # зарезервированными: с ними хук «падает закрыто», поэтому их тут нет.
    ev.PERMISSION_REQUEST: frozenset({forms.ALLOW, forms.DENY}),
    ev.SUBAGENT_START: frozenset({forms.CONTEXT}),
    ev.PROMPT: frozenset({forms.CONTEXT, forms.BLOCK, forms.QUESTION}),
    ev.PRE_TOOL: frozenset({forms.CONTEXT, forms.ALLOW, forms.DENY, forms.QUESTION}),
    ev.POST_TOOL: frozenset({forms.CONTEXT, forms.BLOCK, forms.QUESTION}),
    ev.STOP: frozenset({forms.BLOCK, forms.QUESTION}),
}

# Чего у Codex нет вовсе, каким бы событием ни пришло. Подменять показанный
# человеку текст ему нечем: события «ответ и мысль модели» у него нет, а поля
# показа нет ни у одного его события (замер 18.09.2026, дока хуков).
UNSUPPORTED_FORMS = {
    forms.UPDATED_DISPLAY: 'у Codex нет ни события «ответ и мысль модели», ни поля подмены '
                           'показанного человеку текста: переводить форму некуда',
}

UNSUPPORTED_NOTE = {
    ev.SESSION_START: 'обёртка Codex этапа 3 несёт только контекст на старте сессии',
    ev.AFTER_COMPACT: 'обёртка Codex этапа 3 несёт только контекст после сжатия',
    ev.PERMISSION_REQUEST: 'Codex на запросе разрешения принимает только «разрешить» и '
                           '«запретить»: вопроса человеку и контекста там нет (замер)',
    ev.SUBAGENT_START: 'Codex на старте подагента принимает только контекст (замер)',
    ev.PROMPT: 'Codex на реплике принимает контекст и блок',
    ev.PRE_TOOL: 'Codex перед вызовом принимает контекст, allow и deny; ask и updatedInput не поддержаны',
    ev.POST_TOOL: 'Codex после вызова принимает контекст и блок',
    ev.STOP: 'Codex на остановке принимает блок продолжения, не additionalContext',
}


def unified_of(raw: Mapping[str, Any]) -> str:
    """Какое единое событие пришло."""
    codex_name = raw.get('hook_event_name')
    group = CODEX_EVENTS.get(codex_name)
    if group is None:
        raise ValueError(
            f'событие Codex {codex_name!r} обёртке неизвестно; '
            f'есть {sorted(CODEX_EVENTS)}'
        )
    if codex_name == 'SessionStart':
        return ev.AFTER_COMPACT if raw.get('source') == COMPACT_SOURCE else ev.SESSION_START
    return group[0]


def human_answer(raw: Mapping[str, Any]) -> Any:
    # Замер 04.11.2026: sync отдаёт JSON-строку answers по id вопроса.
    # Async отдаёт только accepted; ответ придёт настоящей репликой позже.
    if raw.get('tool_name') != 'request_user_input':
        return None
    result = raw.get('tool_response')
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, Mapping) or not isinstance(result.get('answers'), Mapping):
        return None
    inputs = raw.get('tool_input')
    questions = inputs.get('questions') if isinstance(inputs, Mapping) else None
    if not isinstance(questions, list):
        return None
    answers = {}
    for question in questions:
        if not isinstance(question, Mapping) or not isinstance(question.get('question'), str):
            continue
        answer = result['answers'].get(question.get('id'))
        values = answer.get('answers') if isinstance(answer, Mapping) else None
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            answers[question['question']] = values
    return answers or None


def to_event(raw: Mapping[str, Any]) -> Event:
    """Событие Codex — в единый формат."""
    return Event(
        event=unified_of(raw),
        session_id=str(raw.get('session_id') or ''),
        cwd=str(raw.get('cwd') or ''),
        harness=HARNESS,
        raw=raw,
        tool_name=raw.get('tool_name'),
        tool_input=raw.get('tool_input'),
        tool_result=raw.get('tool_response'),
        prompt_text=raw.get('prompt'),
        human_answer=human_answer(raw),
        message_text=raw.get('last_assistant_message'),
        # замер 18.09.2026: вариантов для человека Codex на запросе разрешения
        # не даёт вовсе — вопрос человеку лежит текстом внутри `tool_input`.
        # Подагента он называет той же парой полей, что и Claude.
        agent_id=raw.get('agent_id'),
        agent_type=raw.get('agent_type'),
    )


# Ключ, которым обёртка помечает ответ голым текстом: на старте сессии весь
# stdout хука уезжает в контекст как есть (замер 17.09.2026), структура там не
# нужна. Остальные события отвечают структурой `hookSpecificOutput`.
TEXT_KEY = 'text'


def _hook_specific(unified: str, fields: dict) -> dict:
    return {'hookSpecificOutput': {'hookEventName': CODEX_BY_EVENT[unified], **fields}}


def translate(unified: str, response: Response, slug: str = '') -> Delivery:
    """Единый ответ — в форму Codex."""
    if unified not in SUPPORTED:
        raise ValueError(f'события {unified!r} обёртка Codex этапа 3 не несёт')
    if response.kind == forms.SILENCE:
        return Delivery()
    if response.kind in UNSUPPORTED_FORMS:
        return Delivery(supported=False, note=UNSUPPORTED_FORMS[response.kind])
    if response.kind not in SUPPORTED[unified]:
        return Delivery(supported=False, note=UNSUPPORTED_NOTE[unified])

    if unified in (ev.PROMPT, ev.PRE_TOOL, ev.POST_TOOL, ev.STOP):
        return translate_review_event(unified, response, slug)
    if unified == ev.PERMISSION_REQUEST:
        # Поля причины у «разрешить» в схеме Codex нет, как и у Claude: она
        # остаётся в следе.
        decision = (
            {'behavior': 'allow'}
            if response.kind == forms.ALLOW
            else {'behavior': 'deny', 'message': response.reason}
        )
        return Delivery(payload=_hook_specific(unified, {'decision': decision}))
    if unified == ev.SUBAGENT_START:
        return Delivery(payload=_hook_specific(unified, {'additionalContext': response.text}))
    return Delivery(payload={TEXT_KEY: response.text})


def translate_review_event(unified: str, response: Response, slug: str) -> Delivery:
    if response.kind == forms.QUESTION:
        options = '\n'.join(response.options)
        text = (f'Модулю «{slug}» нужен ответ человека. Задай вопрос кнопками '
                f'инструментом request_user_input_async. Вопрос: {response.text}\n{options}\n'
                'accepted подтверждает только показ вопроса, а не выбор человека.')
        if unified == ev.PRE_TOOL:
            return translate_review_event(unified, forms.Deny(text), slug)
        if unified == ev.STOP:
            return translate_review_event(unified, forms.Block(text), slug)
        return translate_review_event(unified, forms.Context(text), slug)
    if response.kind == forms.BLOCK:
        return Delivery(payload={'decision': 'block', 'reason': response.reason})
    if response.kind == forms.CONTEXT:
        return Delivery(payload=_hook_specific(unified, {'additionalContext': response.text}))
    decision = 'deny' if response.kind == forms.DENY else 'allow'
    return Delivery(payload=_hook_specific(unified, {
        'permissionDecision': decision, 'permissionDecisionReason': response.reason}))


def emit(delivery: Delivery, stdout=None, stderr=None) -> int:
    """Напечатать ответ в форме Codex и вернуть код выхода."""
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    if delivery.note:
        stderr.write(delivery.note + '\n')
    if delivery.payload is not None:
        if TEXT_KEY in delivery.payload:
            stdout.write(delivery.payload[TEXT_KEY])
        else:
            stdout.write(json.dumps(delivery.payload, ensure_ascii=False))
    return delivery.exit_code


def run_hook(entry_file: str, module_class: type[Module], argv: list[str] | None = None,
             stdin=None, stdout=None, stderr=None, env: Mapping[str, str] | None = None) -> int:
    """Провести модуль по одному событию Codex и напечатать ответ."""
    parser = argparse.ArgumentParser(description='Обёртка Codex вокруг одного модуля Джарвиса')
    parser.add_argument('--state-dir', type=Path, default=None)
    parser.add_argument(
        '--event',
        required=True,
        action='append',
        dest='events',
        help='единое имя события, на которое стоит строка',
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    stdin = sys.stdin if stdin is None else stdin
    raw = json.loads(stdin.read())
    event = to_event(raw)
    if event.event not in args.events:
        siblings = CODEX_EVENTS.get(raw.get('hook_event_name'), ())
        if [name for name in args.events if name not in siblings]:
            raise ValueError(
                f'строка зарегистрирована на «{", ".join(args.events)}», а пришло '
                f'«{event.event}»: настройки харнеса разошлись с установкой'
            )
        return 0

    module_dir = Path(entry_file).resolve().parents[1]
    manifest = manifest_reader.load(module_dir)
    outcome = run(
        module_class(),
        manifest,
        event,
        Storage(args.state_dir or default_state_dir(module_dir), event.session_id),
        translate=lambda unified, response: translate(unified, response, slug=manifest.slug),
        module_dir=module_dir,
        personal_config=Path(PERSONAL_CONFIG).expanduser(),
        source_config=modules_dir(module_dir) / mode_reader.SOURCE_CONFIG_NAME,
        env=env,
    )
    return emit(outcome.delivery, stdout=stdout, stderr=stderr)
