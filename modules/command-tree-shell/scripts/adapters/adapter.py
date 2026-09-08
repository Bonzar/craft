#!/usr/bin/env python3
"""Адаптер возможности `command_tree` под оболочку: контракт `call(event, args)`.

call(событие, аргументы) -> дерево команды либо {"unsupported": "shfmt: …"}.
Текст команды берётся из `args["command"]`, а без него — из входа вызова
(`event["input"]["command"]`): имя этого поля знает адаптер оболочки, и больше
никто.

Разбор делает ВНЕШНИЙ `shfmt --to-json` (mvdan/sh). Его нет или он не разобрал
строку (незакрытая кавычка) — ответ `unsupported` С ТЕКСТОМ ОШИБКИ, и никакой
резки регулярками взамен (решение 14): догадка на месте разбора и есть тот
фолбэк, ради устранения которого адаптер заведён.

Путь к `shfmt` берётся из PATH, а переопределяется `SHFMT` — им же кейсы
показывают адаптеру пустое окружение, не трогая настоящий PATH.

Запуск файлом — та же функция для чужеязычного потребителя: JSON события и
аргументов на stdin, JSON ответа на stdout.
"""

import importlib.util
import json
import os
import subprocess
import sys

# Соседний файл грузится ОТ ПУТИ, а не через `sys.path`: адаптера зовёт обёртка
# пакета, и встань его каталог первым на путь импорта — он затенил бы общее (об
# этом прямо предупреждает шапка runtime/wrapper.py.tmpl), а `tree` второго
# адаптера подменил бы этот.
def _sibling(name):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "%s.py" % name)
    spec = importlib.util.spec_from_file_location("command_tree_shell_%s" % name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


normalize = _sibling("tree").normalize

# Разбор не должен вешать событие: команда бывает длинной, но не бесконечной.
TIMEOUT_SEC = 5


def call(event, args):
    text = _command(event, args)
    if not text.strip():
        return {"source": text, "statements": []}
    parsed, bad = _parse(text)
    if bad:
        return {"unsupported": "shfmt: %s" % bad}
    answer = normalize(parsed)
    # Исходный текст едет ВМЕСТЕ с деревом: дерево — его вид, и потребителю,
    # которому нужен сам текст (искомое внутри кавычек словом не становится),
    # иначе пришлось бы знать, из какого поля вызова его достать. Знать это —
    # дело адаптера оболочки, а не того, кто его зовёт.
    answer["source"] = text
    return answer


def _command(event, args):
    value = (args or {}).get("command")
    if isinstance(value, str):
        return value
    value = ((event or {}).get("input") or {}).get("command")
    return value if isinstance(value, str) else ""


def _parse(text):
    """Разбор строки → (дерево, причина отказа). Причина непуста — дерева нет."""
    binary = os.environ.get("SHFMT") or "shfmt"
    try:
        done = subprocess.run([binary, "--to-json"], input=text.encode("utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=TIMEOUT_SEC)
    except OSError as bad:
        return (None, "нет разбора оболочки (%s): %s" % (binary, bad))
    except subprocess.SubprocessError as bad:
        return (None, "разбор оболочки не ответил: %s" % bad)
    if done.returncode != 0:
        return (None, done.stderr.decode("utf-8", "replace").strip() or "код %d" % done.returncode)
    try:
        return (json.loads(done.stdout.decode("utf-8")), "")
    except ValueError as bad:
        return (None, "разбор оболочки вернул не JSON: %s" % bad)


def main():
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except ValueError as bad:
        request = {}
        sys.stderr.write("[command-tree-shell] запрос не разобран: %s\n" % bad)
    if not isinstance(request, dict):
        request = {}
    answer = call(request.get("event") or {}, request.get("args") or {})
    sys.stdout.write(json.dumps(answer, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
