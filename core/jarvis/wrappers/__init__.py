"""Обёртки: слой вокруг одного модуля для одного харнеса.

Принять событие, привести к единому формату, дать модулю хранилище, напечатать
ответ в форме харнеса. Всё остальное делают модуль и библиотека.

Какой обёрткой говорить, модуль не решает и не знает: это говорит строка хука.
Установщик знает, куда ставит, и пишет в строку `--harness claude` или
`--harness codex`, а точка входа зовёт `run_hook` отсюда. Поэтому один и тот же
модуль без поля `harness` работает в обоих харнесах, а не говорит с обоими
обёрткой одного.

Без аргумента или с незнакомым значением обёртка не подставляется: это дефект
раскладки, и молчаливый выбор Claude прятал бы его до первого расхождения форм
ответа. Такой запуск печатает причину в stderr и пишет строку следа — по следу
видно, что модуль запускался и почему ничего не ответил.
"""

import json
import sys
from pathlib import Path
from typing import Any, Mapping

from .. import manifest as manifest_reader
from .. import mode as mode_reader
from ..autonomy import is_autonomous
from ..module import Module
from ..registry import modules_dir
from ..storage import Storage, default_state_dir
from ..trace import Trace
from . import claude, codex

HARNESS_FLAG = '--harness'
HARNESSES = {claude.HARNESS: claude, codex.HARNESS: codex}

# Код выхода отказа. Не 2: двойка у Claude на «перед вызовом» и «запросе
# разрешения» значит «запретить», и дефект раскладки запрещал бы вызовы вместо
# того, чтобы просто быть заметным (дока).
REFUSAL_EXIT = 1
NOT_DELIVERED = 'обёртка не выбрана: строка хука не назвала харнес'


def split_harness(argv: list[str]) -> tuple[str | None, list[str]]:
    """Вынуть харнес из строки хука; остальные аргументы едут его обёртке."""
    harness: str | None = None
    rest: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == HARNESS_FLAG:
            harness = argv[index + 1] if index + 1 < len(argv) else None
            index += 2
            continue
        if argument.startswith(HARNESS_FLAG + '='):
            harness = argument.split('=', 1)[1]
            index += 1
            continue
        rest.append(argument)
        index += 1
    return harness, rest


def refusal_reason(harness: str | None) -> str:
    """Почему модуль не сделал хода: текст один и для stderr, и для следа."""
    named = 'харнес не назван' if not harness else f'харнес «{harness}» неизвестен'
    return (
        f'Строка хука не выбрала обёртку: {named}. '
        f'Обёртку называет строка хука аргументом {HARNESS_FLAG}, '
        f'известные харнесы: {", ".join(sorted(HARNESSES))}. '
        'Модуль на этом событии не ответил: переустанови модуль установщиком.'
    )


def _record_refusal(entry_file: str, raw: Mapping[str, Any], module_class: type[Module],
                    reason: str, env: Mapping[str, str] | None) -> None:
    """Строка следа об отказе, с тем же составом полей, что у обычного хода."""
    module_dir = Path(entry_file).resolve().parents[1]
    manifest = manifest_reader.load(module_dir)
    storage = Storage(default_state_dir(), str(raw.get('session_id') or ''))
    decision = mode_reader.read(
        manifest.slug,
        storage,
        Path(mode_reader.PERSONAL_CONFIG).expanduser(),
        modules_dir(module_dir) / mode_reader.SOURCE_CONFIG_NAME,
    )
    Trace(storage).write(
        event=str(raw.get('hook_event_name') or ''),
        module=manifest.slug,
        module_class=module_class.__name__,
        response='error',
        reason=reason,
        mode_enabled=decision.enabled,
        mode_source=decision.source,
        autonomous=is_autonomous(env),
        delivered=False,
        not_delivered_reason=NOT_DELIVERED,
    )


def run_hook(entry_file: str, module_class: type[Module], argv: list[str] | None = None,
             stdin=None, stdout=None, stderr=None, env: Mapping[str, str] | None = None) -> int:
    """Отдать событие обёртке, которую назвала строка хука."""
    argv = (sys.argv[1:] if argv is None else argv)
    harness, rest = split_harness(argv)
    wrapper = HARNESSES.get(harness)
    if wrapper is not None:
        return wrapper.run_hook(entry_file, module_class, argv=rest, stdin=stdin,
                                stdout=stdout, stderr=stderr, env=env)

    stdin = sys.stdin if stdin is None else stdin
    stderr = sys.stderr if stderr is None else stderr
    reason = refusal_reason(harness)
    stderr.write(reason + '\n')
    # Событие читается и здесь: без него у отказа нет ни сессии, ни имени
    # события, а значит и строки следа. Не разобралось — это тоже в stderr, а
    # не молча: иначе отказ выглядел бы как хук, который не запускали.
    try:
        raw = json.loads(stdin.read())
    except (ValueError, OSError) as error:
        stderr.write(f'След об отказе не записан: событие не разобрано ({error}).\n')
        return REFUSAL_EXIT
    _record_refusal(entry_file, raw, module_class, reason, env)
    return REFUSAL_EXIT
