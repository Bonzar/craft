"""Минимальная обёртка Codex: старт сессии, «после сжатия» и три заложенных события.

Этап 3 везёт стартовый контекст в оба харнеса, поэтому здесь ровно столько
Codex, сколько нужно базе стартового контекста, плюс те из трёх заложенных
заранее событий, что у Codex есть: запрос разрешения и старт подагента. Полная
обёртка со всеми событиями и всеми формами ответа — этап 7; чего тут нет,
названо в `UNSUPPORTED_NOTE`, а не подменено соседней формой.

«Ответа и мысли модели» у Codex нет вовсе: в списке его хуков такого события
не значится (замер 18.09.2026), поэтому в карте его нет и установщик
предупреждает, что модуль на нём в Codex не сработает.

Факты о харнесе помечены источником: «дока» — схема протокола app-server,
снятая `codex app-server generate-json-schema` (codex-cli 0.154.0), и страница
хуков https://learn.chatgpt.com/docs/hooks (снята 18.09.2026); «замер» —
проверено прогоном в этом окружении 17 и 18.09.2026.

Замеры 17.09.2026 (codex-cli 0.154.0, контейнер облачной сессии):

- хуку на stdin приходит JSON тех же полей, что у Claude: `hook_event_name`,
  `session_id`, `cwd`, `source`, `transcript_path`, `model`, `permission_mode`;
- stdout хука старта доходит до модели нулевым ходом: модель повторила
  положенный хуком маркер;
- хук без доверия молча не исполняется: строка в конфиге есть, процесса нет и
  сообщения тоже;
- потолок ответа снимается ключом `additionalContextLimit = 0` в строке хука
  (дока: `null` — 2 500 токенов, `0` — без потолка).
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
}
CODEX_BY_EVENT = {
    ev.SESSION_START: 'SessionStart',
    ev.AFTER_COMPACT: 'SessionStart',
    ev.PERMISSION_REQUEST: 'PermissionRequest',
    ev.SUBAGENT_START: 'SubagentStart',
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
CONTEXT_EVENTS = frozenset({'SessionStart', 'SubagentStart'})

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
}

UNSUPPORTED_NOTE = {
    ev.SESSION_START: 'обёртка Codex этапа 3 несёт только контекст на старте сессии',
    ev.AFTER_COMPACT: 'обёртка Codex этапа 3 несёт только контекст после сжатия',
    ev.PERMISSION_REQUEST: 'Codex на запросе разрешения принимает только «разрешить» и '
                           '«запретить»: вопроса человеку и контекста там нет (замер)',
    ev.SUBAGENT_START: 'Codex на старте подагента принимает только контекст (замер)',
}


def unified_of(raw: Mapping[str, Any]) -> str:
    """Какое единое событие пришло."""
    codex_name = raw.get('hook_event_name')
    group = CODEX_EVENTS.get(codex_name)
    if group is None:
        raise ValueError(
            f'событие Codex {codex_name!r} обёртке этапа 3 неизвестно; '
            f'есть {sorted(CODEX_EVENTS)}'
        )
    if codex_name == 'SessionStart':
        return ev.AFTER_COMPACT if raw.get('source') == COMPACT_SOURCE else ev.SESSION_START
    return group[0]


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
    if response.kind not in SUPPORTED[unified]:
        return Delivery(supported=False, note=UNSUPPORTED_NOTE[unified])

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
        Storage(default_state_dir(), event.session_id),
        translate=lambda unified, response: translate(unified, response, slug=manifest.slug),
        module_dir=module_dir,
        personal_config=Path(PERSONAL_CONFIG).expanduser(),
        source_config=modules_dir(module_dir) / mode_reader.SOURCE_CONFIG_NAME,
        env=env,
    )
    return emit(outcome.delivery, stdout=stdout, stderr=stderr)
