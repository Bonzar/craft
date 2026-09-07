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
