#!/usr/bin/env python3
"""База стартового контекста Claude: собирает у поставщиков и печатает кусками.

База знает, как её харнес принимает текст, и не знает, откуда текст. Текст дают
поставщики — адаптеры с `for = ["start-context-*"]`: у кодового зовётся функция
`provide(event, storage)`, у данных читаются файлы `data/*.md`. Кто поставщик,
база выясняет поиском адаптеров по своему slug'у — это единственное, что у баз
общее, и потому лежит в ядре.

Почему копии, а не одна строка (замеры 16–17.09.2026, Claude Code 2.1.274):

- вывод хука Claude режет на 10 000 знаков на каждый процесс, в знаках;
- куски нескольких хуков ложатся в порядке завершения процессов, поэтому
  порядок задаётся цепочкой: копия печатает свой кусок только после того, как
  предыдущая напечатала свой и вышла;
- файл с импортом на старте не годится: импорты читаются вместе с запуском
  хуков, и хук записать файл не успевает.

Копия 1 ставит маркер готовности, собирает, пишет состояние прогона и печатает
первый кусок. Копии 2..N ждут маркер готовности, состояние своего поколения и
маркер «кусок i−1 напечатан», печатают свой и ставят свой маркер. Кому куска не
досталось — молчат. Текст больше ёмкости цепочки обрезается с пометкой в конце.

Зона сессии переживает событие, поэтому маркеры прошлых прогонов в ней лежат.
Прогоны разных событий разведены именами файлов, прогоны одного события —
временем и поколением: копия берёт только маркер не старше собственного старта
и перед самой печатью сверяет поколение ещё раз. Не сошлось — копия молчит, а
не печатает чужой кусок.
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import registry  # noqa: E402
from jarvis.manifest import copy_index  # noqa: E402
from jarvis.wrappers import claude  # noqa: E402

# замер: потолок вывода хука — 10 000 знаков на процесс. Берём 9 900: сотня
# знаков запаса на случай, если потолок считается не ровно тем же способом.
CHUNK = 9_900
STATE_FILE = 'start-context-{event}.json'
READY_FILE = 'start-context-ready-{event}.json'
PRINTED_FILE = 'start-context-printed-{event}-{number}.json'
JOURNAL_FILE = 'start-context.jsonl'
# Ждём ровно столько: дольше держать старт сессии смысла нет, а не дождавшись,
# копия молчит — это законный ответ.
WAIT_SECONDS = 60.0
POLL_SECONDS = 0.05
# Запас на разницу в старте процессов одного прогона: харнес запускает копии
# разом, но интерпретатор поднимается не мгновенно.
FRESH_SLACK = 2.0
# Дольше ждать выхода предыдущего процесса незачем: он уже напечатал.
EXIT_WAIT_SECONDS = 5.0
TRUNCATED_NOTE = '\n\n[стартовый контекст обрезан: он длиннее цепочки копий]'
PROVIDE = 'provide'
DATA_GLOB = '*.md'


def provider_text(adapter, event, storage) -> str:
    """Текст одного поставщика: у кодового — функция, у данных — файлы как есть."""
    library = registry.library_of(adapter)
    if library is not None:
        provide = getattr(library.load(), PROVIDE, None)
        if provide is None:
            raise AttributeError(f'в lib поставщика «{adapter.slug}» нет функции {PROVIDE}')
        return provide(event, storage) or ''
    data = registry.data_of(adapter)
    if data is None:
        return ''
    pieces = [path.read_text(encoding='utf-8') for path in sorted(data.glob(DATA_GLOB))]
    return '\n\n'.join(pieces)


def gather(runtime, event) -> tuple[str, list[dict]]:
    """Склейка по slug поставщика, с заголовком-разделителем.

    Упавший поставщик не уносит с собой остальных: он попадает в учёт пустым и
    с причиной, а база отдаёт то, что собралось. Один сломанный поставщик —
    не повод остаться вовсе без стартового контекста.
    """
    pieces = []
    accounted = []
    for adapter in registry.adapters(runtime.module_dir, runtime.manifest.slug):
        try:
            text = (provider_text(adapter, event, runtime.storage) or '').strip()
            error = None
        except Exception as failure:  # noqa: BLE001 — причина уходит в учёт
            text, error = '', f'{type(failure).__name__}: {failure}'
        accounted.append({'slug': adapter.slug, 'chars': len(text), 'error': error})
        if text:
            pieces.append(f'## {adapter.slug}\n\n{text}')
    return '\n\n'.join(pieces), accounted


def cut(text: str, size: int, count: int) -> tuple[list[str], bool]:
    """Резка на куски по ёмкости копии. Лишнее — обрезается с пометкой."""
    pieces = [text[start:start + size] for start in range(0, len(text), size)]
    if len(pieces) <= count:
        return pieces, False
    pieces = pieces[:count]
    pieces[-1] = pieces[-1][: max(0, size - len(TRUNCATED_NOTE))] + TRUNCATED_NOTE
    return pieces, True


def still_running(pid: int) -> bool:
    """Жив ли процесс предыдущей копии. Умер — его кусок уже напечатан."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Module(jarvis.Module):
    """Копия базы. Номер — из своего slug'а, всего копий — из шапки."""

    # Маркер «кусок напечатан» ставится после того, как ответ ушёл в stdout, —
    # иначе следующая копия печатала бы, пока эта ещё пишет.
    _printed: tuple[jarvis.Storage, str, int, str] | None = None

    def handle(self, event, runtime):
        number = copy_index(runtime.manifest.slug)
        total = runtime.manifest.copies
        started = time.time()
        storage = runtime.storage
        if number == 1:
            return self._first(event, runtime, storage, number, total, started)
        return self._next(event, runtime, storage, number, started)

    # --- копия 1: собрать, разложить, напечатать первый кусок ---

    def _first(self, event, runtime, storage, number, total, started):
        generation = uuid.uuid4().hex
        # Маркер готовности ставится до сборки: остальные копии уже ждут, и им
        # нужно как можно раньше узнать поколение этого прогона. Состояние они
        # дожидаются отдельно и по тому же поколению.
        storage.write_json(name(READY_FILE, event), {'generation': generation, 'at': time.time()})
        text, accounted = gather(runtime, event)
        pieces, truncated = cut(text, CHUNK, total)
        storage.write_json(name(STATE_FILE, event), {
            'generation': generation,
            'event': event.event,
            'at': started,
            'total_chars': len(text),
            'copies': total,
            'chunks': pieces,
            'truncated': truncated,
            'providers': accounted,
        })
        if not pieces:
            note(storage, number, 'silence', 'поставщики не дали текста')
            return jarvis.Silence()
        note(storage, number, 'context',
             f'собрано {len(text)} знаков, кусков {len(pieces)}, обрезано: {truncated}')
        type(self)._printed = (storage, event.event, number, generation)
        return jarvis.Context(pieces[0])

    # --- копии 2..N: дождаться очереди и напечатать свой кусок ---

    def _next(self, event, runtime, storage, number, started):
        ready = wait_for(
            lambda: storage.read_json(name(READY_FILE, event), default=None),
            lambda mark: mark.get('at', 0) >= started - FRESH_SLACK,
            started,
        )
        if ready is None:
            note(storage, number, 'silence', 'маркер готовности не появился за отведённое время')
            return jarvis.Silence()
        generation = ready['generation']

        state = wait_for(
            lambda: storage.read_json(name(STATE_FILE, event), default=None),
            lambda mark: mark.get('generation') == generation,
            started,
        )
        if state is None:
            note(storage, number, 'silence', 'состояние прогона не появилось за отведённое время')
            return jarvis.Silence()

        pieces = state.get('chunks') or []
        if number > len(pieces):
            note(storage, number, 'silence', f'куска нет: кусков {len(pieces)}')
            return jarvis.Silence()

        previous = wait_for(
            lambda: storage.read_json(name(PRINTED_FILE, event, number - 1), default=None),
            lambda mark: mark.get('generation') == generation,
            started,
        )
        if previous is None:
            note(storage, number, 'silence', 'предыдущая копия не отметилась за отведённое время')
            return jarvis.Silence()
        wait_exit(previous.get('pid'))

        fresh = storage.read_json(name(READY_FILE, event), default=None)
        if not isinstance(fresh, dict) or fresh.get('generation') != generation:
            note(storage, number, 'silence', 'пока ждали, начался следующий прогон')
            return jarvis.Silence()

        note(storage, number, 'context', f'кусок {number} из {len(pieces)}')
        type(self)._printed = (storage, event.event, number, generation)
        return jarvis.Context(pieces[number - 1])

    @classmethod
    def mark_printed(cls) -> None:
        """Отметить, что кусок напечатан: зовётся после того, как ответ ушёл."""
        if cls._printed is None:
            return
        storage, event, number, generation = cls._printed
        storage.write_json(
            PRINTED_FILE.format(event=event, number=number),
            {'generation': generation, 'pid': os.getpid(), 'at': time.time()},
        )


def name(template: str, event, number: int | None = None) -> str:
    """Имя в зоне сессии: прогоны разных событий не путаются между собой."""
    return template.format(event=event.event, number=number)


def wait_for(read, good, started):
    """Дождаться записи, которая подходит. Не дождались — None, и копия молчит."""
    deadline = started + WAIT_SECONDS
    while time.time() < deadline:
        mark = read()
        if isinstance(mark, dict) and good(mark):
            return mark
        time.sleep(POLL_SECONDS)
    return None


def wait_exit(pid) -> None:
    """Дождаться выхода предыдущего процесса.

    Claude складывает куски в порядке завершения процессов, а маркер ставится
    ещё живым процессом: без этого ожидания две копии могли бы завершиться в
    обратном порядке и куски легли бы наоборот.
    """
    if not isinstance(pid, int):
        return
    deadline = time.time() + EXIT_WAIT_SECONDS
    while time.time() < deadline and still_running(pid):
        time.sleep(POLL_SECONDS)


def note(storage, number, answer, why) -> None:
    """Журнал цепочки: почему копия ответила именно так. Лежит в зоне сессии."""
    storage.append_line(JOURNAL_FILE, json.dumps(
        {'at': time.time(), 'copy': number, 'answer': answer, 'why': why},
        ensure_ascii=False,
    ))


if __name__ == '__main__':
    code = claude.run_hook(__file__, Module)
    sys.stdout.flush()
    Module.mark_printed()
    sys.exit(code)
