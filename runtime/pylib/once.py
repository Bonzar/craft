"""Уступка второму вызову того же события (перенос .claude/hooks/lib/once.js).

Модуль зарегистрирован строкой на событие, но регистраций у одного дерева бывает
две — своя и командного пресета, — и тогда на одно событие приходят два процесса.
Без уступки модуль отработает дважды: двойная директива, двойной отказ, двойной
счётчик.

Занятие события — каталогом: его создание атомарно, поэтому из двух
одновременных вызовов ровно один создаёт метку, второй видит занятое.

Ключ метки — имя модуля, ИМЯ СОБЫТИЯ и ключ события. Имя события обязательно: у
одного вызова инструмента идентификатор общий на событии до вызова и после него,
и по ключу без имени модуль гасил бы своё же событие после вызова меткой,
оставленной до.

У событий с идентификатором вызова метка не протухает — другого такого вызова не
будет, — и её убирает уборщик по возрасту. У событий без идентификатора ключ
считается по байтам события, и срок метки — единицы секунд: два процесса на одно
событие приходят одновременно, а следующее такое же событие приходит позже.
"""

import os
import time

MARK_PREFIX = "jarvis-once."
MARK_TTL_S = 60 * 60
CONTENT_TTL_S = 5
SWEEP_EVERY_S = 10 * 60


def _safe(text):
    return "".join(c if (c.isalnum() or c in "_-") else "_" for c in str(text))[:64]


def _sweep(state_dir):
    """Метки старше срока сносятся, но не чаще раза в SWEEP_EVERY_S: право на
    уборку забирает тот, кто обновил отметку. Без этого метки копились бы по
    каталогу на каждый вызов до конца жизни машины."""
    stamp = os.path.join(state_dir, MARK_PREFIX + "sweep")
    now = time.time()
    try:
        if now - os.stat(stamp).st_mtime < SWEEP_EVERY_S:
            return
    except OSError:
        pass
    try:
        with open(stamp, "w", encoding="utf-8"):
            pass
    except OSError:
        return
    try:
        names = os.listdir(state_dir)
    except OSError:
        return
    for name in names:
        if not name.startswith(MARK_PREFIX) or name.endswith("sweep"):
            continue
        path = os.path.join(state_dir, name)
        try:
            if now - os.stat(path).st_mtime > MARK_TTL_S:
                os.rmdir(path)
        except OSError:
            continue


def take(module, event, state_dir):
    """True — работай; False — уступи: событие уже занято другим процессом.

    `event` — каноническое событие целиком: из него берутся имя события, ключ и
    признак «есть ли идентификатор вызова»."""
    name = _safe(module)
    key = (event or {}).get("key") or ""
    if not key or not state_dir:
        # Ключа нет — отличить второй вызов от следующего события нечем, и
        # уступка молча гасила бы работу. Работаем.
        return True
    has_id = bool(((event or {}).get("call_id") or "").strip())
    mark = os.path.join(
        state_dir,
        "%s%s.%s.%s" % (MARK_PREFIX, name, _safe((event or {}).get("event") or "event"), _safe(key)),
    )
    try:
        os.makedirs(mark)
        _sweep(state_dir)
        return True
    except OSError:
        pass
    try:
        age = time.time() - os.stat(mark).st_mtime
    except OSError:
        return True
    # Метка есть. У события с идентификатором она значима, пока её не снёс
    # уборщик: метка того же возраста, до которой уборка ещё не дошла, обязана
    # значить то же самое, иначе исход зависел бы от того, успел ли кто-то
    # прибраться. У события без идентификатора срок короткий: то же сообщение,
    # повторённое позже, — уже другое событие.
    ttl = MARK_TTL_S if has_id else CONTENT_TTL_S
    if age <= ttl:
        return False
    now = time.time()
    try:
        os.utime(mark, (now, now))
    except OSError:
        pass
    return True
