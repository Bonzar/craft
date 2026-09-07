"""Состояние модуля на диске: чтение и запись JSON, дозапись JSONL, лок файлом.

Всё под каталогом, который модуль получил аргументом (`state_dir` ядра события):
своего пути этот файл не выводит и окружения не читает.

Читать умеет всегда, писать — атомарно: сосед не должен поймать полуфайл.
Лок — каталогом, потому что его создание атомарно на любой файловой системе;
не достался за отведённое время — правка НЕ делается, и вызывающий об этом
узнаёт по ответу, а не по тишине.
"""

import json
import os
import tempfile
import time


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
