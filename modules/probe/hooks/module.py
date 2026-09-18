#!/usr/bin/env python3
"""Модуль-проба: проходит путь целиком и ничего не решает.

Отвечает молчанием на каждом своём событии и кладёт в след состав события —
какие поля единого формата харнес наполнил. Смысл пробы в этом следе: по нему
видно, что модуль установлен, обёртка довела до него событие, библиотека
прочла режим и признак автономии и записала ответ.

Проба первой встаёт на три заложенных заранее события — запрос разрешения,
старт подагента, ответ и мысль модели. На запросе разрешения она отвечает
молчанием, а не «разрешить» и не «запретить»: проба не имеет права менять
поведение харнеса.

Файл запускает сам харнес — он и есть точка входа модуля. Ядро ищется от пути
к этому файлу: `hooks/` лежит в папке модуля, и там же рядом папка ядра
`_core`. Модуль самодостаточен: установщик для этого не нужен, поэтому он
работает и там, куда его просто скопировали папкой.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis.wrappers import claude  # noqa: E402

# Поля единого события, которые наполняет харнес. Общие для всех событий
# (имя, сессия, каталог, харнес, сырое событие) в состав не считаются: они
# есть всегда, и по ним событие от события не отличить.
EVENT_FIELDS = (
    'tool_name',
    'tool_input',
    'tool_result',
    'error',
    'prompt_text',
    'human_answer',
    'permission_options',
    'agent_id',
    'agent_type',
    'message_text',
)
EMPTY = (None, '', (), {}, [])


def composition(event: jarvis.Event) -> str:
    """Какие поля события харнес наполнил — строкой для следа."""
    filled = [name for name in EVENT_FIELDS if getattr(event, name) not in EMPTY]
    return 'состав события: ' + (', '.join(filled) if filled else 'только общие поля')


class Module(jarvis.Module):
    """Молчание — законный ответ, и он тоже пишется в след."""

    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        return jarvis.Silence(reason=composition(event))


if __name__ == '__main__':
    sys.exit(claude.run_hook(__file__, Module))
