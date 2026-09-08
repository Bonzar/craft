"""Состояние и настройка слоя на диске: где что лежит, как читать и как писать.

ПУТИ — одна формула на всех: каталог состояния и журнал решений общие с
JS-хуками, и повторяют .claude/hooks/lib/paths.js слово в слово. Живут здесь, а
не в таблице харнеса: про харнес в них нет ничего, а копий стало бы столько,
сколько таблиц.

ЗАПИСЬ — под каталогом, который модуль получил аргументом (`state_dir` ядра
события): сама логика модуля путей не выводит и окружения не читает.

Читать умеет всегда, писать — атомарно: сосед не должен поймать полуфайл.
Лок — каталогом, потому что его создание атомарно на любой файловой системе;
не достался за отведённое время — правка НЕ делается, и вызывающий об этом
узнаёт по ответу, а не по тишине.
"""

import json
import os
import sys
import tempfile
import time


def state_dir():
    """Каталог состояния слоя. Временный выводится ТЕМ ЖЕ порядком переменных,
    что у Node (`os.tmpdir()`: TMPDIR, TMP, TEMP, затем /tmp): `gettempdir()`
    спорит с ним порядком TMP и TEMP, и при заданных обоих след пакета лёг бы в
    журнал, которого никто не читает."""
    override = os.environ.get("CRAFT_STATE_DIR")
    if override:
        return override
    for name in ("TMPDIR", "TMP", "TEMP"):
        value = os.environ.get(name)
        if value:
            return value.rstrip(os.sep) or os.sep
    return "/tmp"


def decision_log(session_id, directory):
    """Путь журнала решений: переопределение сильнее, иначе файл сессии в
    каталоге состояния."""
    override = os.environ.get("CRAFT_DECISION_LOG")
    if override:
        return override
    # Пустой каталог — тот же `state_dir()`, слово в слово как в paths.js: иначе
    # путь вышел бы ОТНОСИТЕЛЬНЫМ, и журнал лёг бы в рабочий каталог сессии.
    return os.path.join(directory or state_dir(), "decisions.%s.jsonl" % (session_id or "default"))


OFF = ("off", "false", "0")


def modes_file():
    """Личный конфиг режимов — старший из двух файловых источников."""
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(config, "jarvis", "modules.toml")


def mode(name, source_root, manifest_mode="on"):
    """Режим модуля и ОТКУДА он взят. Читают это двое — обёртка на каждом событии
    и `jarvis status`, — и читать обязаны одинаково: разъедься они, `status` стал
    бы утверждать «on» про модуль, который обёртка гасит.

    Источники по старшинству: переменная окружения, личный конфиг, `personal/`
    источника, а последним — режим из манифеста. Решает ПЕРВЫЙ, который про ЭТОТ
    модуль что-то говорит: переменная, назвавшая чужой модуль, про наш не
    сказала ничего и решать за него не вправе.

    Нет `tomllib` (он с python 3.11) — файловые источники читать нечем, и это
    называется вслух; но манифест при этом остаётся, и объявленный выключенным
    модуль выключенным и остаётся."""
    listed = [part.strip() for part in os.environ.get("JARVIS_MODULES_OFF", "").split(",") if part.strip()]
    if "all" in listed or "*" in listed or name in listed:
        return ("off", "JARVIS_MODULES_OFF")
    default = ("off" if str(manifest_mode or "on").lower() in OFF else "on", "манифест")
    try:
        import tomllib
    except ImportError:
        sys.stderr.write("[%s] mode из файлов не читается: нужен python 3.11+\n" % name)
        return default
    for path in (modes_file(), os.path.join(source_root or "", "personal", "modules.toml")):
        try:
            with open(path, "rb") as fh:
                table = tomllib.load(fh).get("modules") or {}
        except OSError:
            continue
        except ValueError as bad:
            # Испорченный конфиг — это не «модуль включён». Пропуск называется
            # вслух, иначе выключенный модуль тихо работал бы.
            sys.stderr.write("[%s] %s не читается: %s\n" % (name, path, bad))
            continue
        if name in table:
            return ("off" if str(table[name]).lower() in OFF else "on", path)
    return default


def read_json(path, default=None):
    """Разобранный JSON или default: файла нет, он пуст или испорчен."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    """Атомарная запись: временный файл рядом и переименование поверх."""
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    except OSError:
        return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def append_jsonl(path, record):
    """Дописать строку JSONL. Возвращает, легла ли она: потерянная строка
    решения читалась бы как «решения не было», то есть как проход.

    Разделители БЕЗ пробелов — как у JSON.stringify: журнал решений общий с
    JS-хуками, и строка пакета обязана выглядеть в нём так же, как соседняя."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return True
    except OSError:
        return False


def read_jsonl(path):
    """Разобранные строки; неразборная пропускается, а не роняет чтение."""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return out
    return out


class Lock:
    """Лок каталогом. `taken` говорит, достался ли он: не достался — вызывающий
    обязан не трогать состояние, а не делать вид, что оно под ним."""

    def __init__(self, path, wait_ms=300):
        self.path = path + ".lock"
        self.wait_ms = wait_ms
        self.taken = False

    def __enter__(self):
        deadline = time.monotonic() + self.wait_ms / 1000.0
        while True:
            try:
                os.makedirs(self.path)
                self.taken = True
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    return self
                time.sleep(0.01)

    def __exit__(self, *_):
        if self.taken:
            try:
                os.rmdir(self.path)
            except OSError:
                pass
        return False
