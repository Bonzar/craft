#!/usr/bin/env python3
"""Модуль codex-login: кладёт вход Codex из переменной окружения в ~/.codex/auth.json.

Зачем. В облачной сессии рядом с нами живёт второй агент — `codex`. Клиент в
контейнере предустановлен, а входа у него нет: вход целиком лежит в одном файле
`~/.codex/auth.json`, и его содержимое приезжает настройкой окружения
`CODEX_AUTH_JSON`. Файла в свежем контейнере нет, поэтому `codex` без раскладки
отвечает «Not logged in».

Почему на старте сессии, а не setup-скриптом окружения. Настройки окружения в
setup-скрипт не приезжают (дамп имён переменных 18.09.2026), а в самой сессии
они есть. Значит, всё, что зависит от переменных окружения, раскладывается на
старте сессии — и это тот же случай.

Ответ харнесу — молчание: в контексте модели раскладке файла делать нечего.
Что произошло, видно в следе сессии одной строкой: «разложен», «уже на месте»,
«переменной нет» или «не удалось: причина».

Значения переменной в следе нет и быть не может: это секрет. В след едут только
наши четыре формулировки и текст ошибки файловой системы — в нём путь, но не
содержимое.

Один сценарий модуль умышленно не разбирает: если `codex` сам обновит токен,
файл разойдётся с переменной, и следующая сессия положит поверх значение из
переменной. Разойтись они могут только после того, как access-токен проживёт
свои 240 часов, и чинится это обновлением настройки окружения, а не кодом:
свежего значения модулю взять неоткуда.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import wrappers  # noqa: E402

# Настройка окружения с содержимым auth.json целиком.
ENV_AUTH = 'CODEX_AUTH_JSON'
ENV_HOME = 'HOME'

CODEX_DIR = '.codex'
AUTH_FILE = 'auth.json'
# Каталог входа — только владельцу, файл входа — только владельцу на чтение и
# запись: там лежит живой токен. Те же права ставит себе сам `codex`.
DIR_MODE = 0o700
FILE_MODE = 0o600

LAID = 'разложен'
ALREADY = 'уже на месте'
NO_ENV = 'переменной нет'
FAILED = 'не удалось: {reason}'
NO_HOME = f'в окружении нет {ENV_HOME}'


def auth_path(home: str | None) -> Path:
    """Куда ложится вход. Дом берём из HOME процесса, как его видит сам codex."""
    if not home:
        raise ValueError(NO_HOME)
    return Path(home) / CODEX_DIR / AUTH_FILE


def current(path: Path) -> str | None:
    """Что лежит по пути сейчас. Файла нет — None; прочие беды идут наверх."""
    try:
        return path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return None


def lay_out(path: Path, value: str) -> None:
    """Записать вход: временный файл рядом и переименование поверх.

    Временный файл создаётся сразу с правами 600, а не получает их после
    записи: между созданием и `chmod` секрет полежал бы открытым.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    tmp = path.with_name(f'{path.name}.tmp-{os.getpid()}')
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(value)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class Module(jarvis.Module):
    """Молчание на любом исходе: разница между исходами живёт в следе."""

    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        value = os.environ.get(ENV_AUTH) or ''
        if not value:
            return jarvis.Silence(reason=NO_ENV)
        try:
            path = auth_path(os.environ.get(ENV_HOME))
            if current(path) == value:
                return jarvis.Silence(reason=ALREADY)
            lay_out(path, value)
        except (OSError, ValueError) as failure:
            # Ошибка не глотается: причина уходит в след с контекстом. Наружу
            # она не летит — упавшая раскладка входа второго агента не повод
            # ронять старт сессии, а ответ харнесу тут в любом исходе молчание.
            return jarvis.Silence(reason=FAILED.format(reason=f'{type(failure).__name__}: {failure}'))
        return jarvis.Silence(reason=LAID)


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
