"""Решение модуля: конструкторы исходов и проверка жёстких зависимостей.

Исходы (решение 17): allow, ask, deny, none — на вызове инструмента; block — на
конце хода. Рядом с решением могут идти reason, add_context, modified_input, у
block — message.

Формы ответа харнеса здесь нет: её собирает таблица харнеса в обёртке.
Недостающая жёсткая зависимость даёт `unsupported` С ИМЕНЕМ (решение 14), а не
молчание и не обходной путь; мягких зависимостей не бывает объявленных — всё,
чего нет в `requires`, по определению мягкое (решение 6).
"""

# Исключающие исходы: те, что дают харнесу ответ и которых он не должен прочитать
# дважды. Список живёт здесь, а не у читателей: их уже трое.
FIRM = ("deny", "ask", "block")

ALLOW = "allow"
ASK = "ask"
DENY = "deny"
NONE = "none"
BLOCK = "block"


def _decision(outcome, reason="", **extra):
    out = {"outcome": outcome, "reason": reason}
    for name, value in extra.items():
        if value is not None:
            out[name] = value
    return out


def allow(reason=""):
    return _decision(ALLOW, reason)


def ask(reason):
    return _decision(ASK, reason)


def deny(reason):
    return _decision(DENY, reason)


def block(reason, message=None):
    return _decision(BLOCK, reason, message=message)


def none(reason="", add_context=None, modified_input=None):
    """«Я не решал»: дописанный контекст или правка входа — не запрет."""
    return _decision(NONE, reason, add_context=add_context, modified_input=modified_input)


def missing_fact(event, requires):
    """Первое из объявленных, чего событие не принесло. Пусто — всё на месте.

    Факт ДАН, когда ключ есть и значение непусто: ноль токенов — факт,
    отсутствие ключа — нет."""
    for fact in requires or []:
        value = (event or {}).get(fact)
        if value is None:
            return fact
        if isinstance(value, str) and value == "":
            return fact
    return ""


def unsupported(capability):
    """Непокрытое называется ИМЕНЕМ — и это ОТВЕТ, а не ошибка и не молчание.

    Исход `none` («я не решал») по вызову верен: модуль ничего не запретил. Но
    отличить его от молчащего модуля надо, иначе непокрытое исчезает из журнала,
    — поэтому имя едет отдельным полем, и след ставит его классом строки."""
    return _decision(NONE, "unsupported: %s" % capability, unsupported=str(capability))


# --- возможности и их реализации -----------------------------------------------
#
# Всё ниже — про то, ЧЕМ закрыта жёсткая зависимость. Стоит здесь, а не шестым
# файлом pylib: вопрос тот же самый, что у `missing_fact` выше, — «объявленное
# есть или его нет», — и разъехаться этим двум ответам нельзя. Потолок pylib
# (пять файлов) держится кейсом, и новый файл был бы находкой ревью.
#
# Зовёт это ОБЁРТКА, а не логика модуля: диск и подпроцесс — её дело, а `decide`
# получает готовое дерево данными (правило 1).

import importlib.util
import os

import state

# Где в пакете лежит адаптер. Одно место на всех: контракт `call(event, args)`
# ищется по имени файла, а не объявляется в манифесте.
ADAPTER = os.path.join("scripts", "adapters", "adapter.py")


def capability_of(name, for_value=""):
    """Возможность пакета — из его имени и `for` (решение 6; полей `provides` нет).

    У адаптера хвост имени повторяет инструмент или харнес из `for`, и он
    снимается: `command-tree-shell` + `for: tool:shell` закрывает `command_tree`.
    Дефисы становятся подчёркиваниями, потому что имя возможности едет в событие
    ПОЛЕМ, а поля канонического события пишутся так же (`call_id`, `state_dir`)."""
    base = str(name or "")
    tail = str(for_value or "").split(":", 1)[1] if ":" in str(for_value or "") else ""
    if tail and base.endswith("-" + tail):
        base = base[: -len(tail) - 1]
    return base.replace("-", "_")


def implementations(capability, roots):
    """Пакеты известных корней, закрывающие возможность: пары (корень, манифест).

    Читается МАНИФЕСТ ИСТОЧНИКА каждого корня — тот, что кладёт установка.
    Центрального индекса нет (решение 12), поэтому адаптер, положенный в
    известный корень, виден со следующего события без пересборки обёрток."""
    found = []
    for root in roots:
        index = state.read_json(os.path.join(root, "modules.index.json"), {}) or {}
        for manifest in index.get("modules") or []:
            if not isinstance(manifest, dict):
                continue
            if capability_of(manifest.get("name", ""), manifest.get("for", "")) == capability:
                found.append((root, manifest))
    return found


def call(capability, event, roots, args=None):
    """Ответ реализации возможности: пара (ответ, чего не хватило).

    Реализации нет вовсе — (None, имя возможности): непокрытое называется именем
    (решение 14). Реализация есть, но адаптера у неё нет — (None, ""): звать
    нечего, зависимость закрыта самим пакетом, и молчание тут верно. Адаптер
    ответил `unsupported` или упал — (None, текст): падение адаптера ответа
    модуля не меняет (решение 25), но и выдумывать значение вместо него нельзя."""
    found = implementations(capability, roots)
    if not found:
        return (None, capability)
    for root, manifest in found:
        path = os.path.join(root, "modules", manifest.get("name", ""), ADAPTER)
        if not os.path.isfile(path):
            continue
        try:
            answer = _load(path).call(event, args or {})
        except Exception as bad:  # noqa: BLE001 — падение адаптера называется словами
            return (None, "%s: %s" % (manifest.get("name", ""), bad))
        if not isinstance(answer, dict):
            return (None, "%s: ответ не словарь" % manifest.get("name", ""))
        if answer.get("unsupported"):
            return (None, str(answer["unsupported"]))
        return (answer, "")
    return (None, "")


def _load(path):
    """Модуль адаптера по пути. Загрузка от ФАЙЛА, а не по имени пакета: корни
    источников лежат где угодно, и на пути импорта их нет."""
    spec = importlib.util.spec_from_file_location("adapter_%s" % abs(hash(path)), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
