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
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))

import jarvis  # noqa: E402
from jarvis import wrappers  # noqa: E402

# Настройка окружения с содержимым auth.json целиком.
ENV_AUTH = 'CODEX_AUTH_JSON'
ENV_HOME = 'HOME'
# Штатный способ показать codex другой каталог входа. Задан — читать и писать
# нужно именно там.
ENV_CODEX_HOME = 'CODEX_HOME'

CODEX_DIR = '.codex'
AUTH_FILE = 'auth.json'
# Каталог входа — только владельцу, файл входа — только владельцу на чтение и
# запись: там лежит живой токен. Те же права ставит себе сам `codex`.
DIR_MODE = 0o700
FILE_MODE = 0o600

LAID = 'разложен'
ALREADY = 'уже на месте'
ALREADY_NARROWED = 'уже на месте, права поправлены'
NO_ENV = 'переменной нет'
FAILED = 'не удалось: {reason}'
NO_HOME = f'в окружении нет {ENV_HOME}'


def auth_path(env) -> Path:
    """Куда ложится вход: CODEX_HOME, если задана, иначе HOME/.codex.

    Порядок тот же, что у самого codex. Писать в HOME при заданной CODEX_HOME
    значило бы разложить вход мимо того места, откуда codex его читает, — и
    отчитаться «разложен», то есть признаком, не различающим исходы.
    """
    codex_home = env.get(ENV_CODEX_HOME)
    if codex_home:
        return Path(codex_home) / AUTH_FILE
    home = env.get(ENV_HOME)
    if not home:
        raise ValueError(NO_HOME)
    return Path(home) / CODEX_DIR / AUTH_FILE


def current(path: Path) -> bytes | None:
    """Что лежит по пути сейчас, байтами. Файла нет — None.

    Байтами, а не текстом: файл с не-UTF-8 байтами — это файл, который от
    переменной отличается, а текстовое чтение бросало бы на нём
    UnicodeDecodeError и уводило такой файл в исход «не удалось» вместо
    перезаписи.
    """
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def narrow(path: Path, mode: int) -> bool:
    """Сузить права до нужных, если они шире. Вернёт, менялось ли что-то.

    Шире — значит есть биты сверх нужных. Уже более узкие права не трогаем:
    сузить их до наших было бы расширением.
    """
    if not stat.S_IMODE(path.stat().st_mode) & ~mode:
        return False
    path.chmod(mode)
    return True


def lay_out(path: Path, value: str) -> None:
    """Записать вход: временный файл рядом и переименование поверх.

    Временный файл создаётся сразу с правами 600, а не получает их после
    записи: между созданием и `chmod` секрет полежал бы открытым.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    # Каталог мог существовать до нас и быть открыт всем: mkdir на таком
    # молчит, а класть туда живой токен нельзя. Правило «каталог 700»
    # держится независимо от того, кто его создал.
    narrow(path.parent, DIR_MODE)
    tmp = path.with_name(f'{path.name}.tmp-{os.getpid()}')
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(value.encode('utf-8'))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def prepare(env) -> str:
    """Положить вход до старта Codex и вернуть безопасную причину результата.

    Хук Claude и локальная обёртка Codex используют одну и ту же операцию. У
    обёртки она идёт *до* процесса Codex, у хука — в облачной сессии, где
    переменные недоступны setup-скрипту. Секрет остаётся только в файле:
    наружу возвращается одна из безопасных причин для следа или сообщения.
    """
    value = env.get(ENV_AUTH) or ''
    if not value:
        return NO_ENV
    try:
        path = auth_path(env)
        if current(path) == value.encode('utf-8'):
            # Содержимое то, а права могли положить чужие руки: файл с живым
            # токеном обязан остаться закрытым.
            narrowed = narrow(path, FILE_MODE)
            narrowed = narrow(path.parent, DIR_MODE) or narrowed
            return ALREADY_NARROWED if narrowed else ALREADY
        lay_out(path, value)
    except (OSError, ValueError) as failure:
        return FAILED.format(reason=f'{type(failure).__name__}: {failure}')
    return LAID


class Module(jarvis.Module):
    """Молчание на любом исходе: разница между исходами живёт в следе."""

    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        return jarvis.Silence(reason=prepare(os.environ))


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
