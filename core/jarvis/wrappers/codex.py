"""Минимальная обёртка Codex: только старт сессии и «после сжатия».

Этап 3 везёт стартовый контекст в оба харнеса, поэтому здесь ровно столько
Codex, сколько нужно базе стартового контекста: событие со stdin переводится в
единый формат, единый ответ — в stdout. Полная обёртка со всеми событиями и
всеми формами ответа — этап 7; чего тут нет, названо в `UNSUPPORTED_NOTE`, а не
подменено соседней формой.

Факты о харнесе помечены источником: «дока» — схема протокола app-server,
снятая `codex app-server generate-json-schema` (codex-cli 0.154.0); «замер» —
проверено прогоном в этом окружении 17.09.2026.

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
}
CODEX_BY_EVENT = {
    ev.SESSION_START: 'SessionStart',
    ev.AFTER_COMPACT: 'SessionStart',
}
# Какие события Codex принимают текст в ход. Пока одно — и оно же несёт оба
# наших: и старт сессии, и «после сжатия».
CONTEXT_EVENTS = frozenset({'SessionStart'})

# Личный конфиг режимов общий для харнесов.
PERSONAL_CONFIG = mode_reader.PERSONAL_CONFIG

# замер: `source` у SessionStart — startup, resume или compact. После сжатия
# Codex перезапускает хук с source=compact, и его stdout доходит до модели
# свежим, а не из кэша старта.
COMPACT_SOURCE = 'compact'

SUPPORTED: dict[str, frozenset[str]] = {
    ev.SESSION_START: frozenset({forms.CONTEXT}),
    ev.AFTER_COMPACT: frozenset({forms.CONTEXT}),
}

UNSUPPORTED_NOTE = {
    ev.SESSION_START: 'обёртка Codex этапа 3 несёт только контекст на старте сессии',
    ev.AFTER_COMPACT: 'обёртка Codex этапа 3 несёт только контекст после сжатия',
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
    )


def translate(unified: str, response: Response, slug: str = '') -> Delivery:
    """Единый ответ — в форму Codex: текстом на stdout."""
    if unified not in SUPPORTED:
        raise ValueError(f'события {unified!r} обёртка Codex этапа 3 не несёт')
    if response.kind == forms.SILENCE:
        return Delivery()
    if response.kind not in SUPPORTED[unified]:
        return Delivery(supported=False, note=UNSUPPORTED_NOTE[unified])
    return Delivery(payload={'text': response.text})


def emit(delivery: Delivery, stdout=None, stderr=None) -> int:
    """Напечатать ответ в форме Codex и вернуть код выхода."""
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    if delivery.note:
        stderr.write(delivery.note + '\n')
    if delivery.payload is not None:
        stdout.write(delivery.payload['text'])
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
