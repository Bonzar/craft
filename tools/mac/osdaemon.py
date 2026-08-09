"""Демон ввода уровня macOS: полный словарь взаимодействий со страницей.

Живёт агентом launchd в графической сессии учётки — только оттуда система пускает
синтетический ввод, и только пока эта сессия держит консоль. Задание кладётся файлом
в каталог песочницы dev-browser, ответ — файл-метка.

Зачем не отладочный протокол: тот округляет координаты до целых, а живая мышь на Retina
даёт дробные, и это видно странице. Здесь координата доезжает как есть, давление при
нажатии настоящее (0.5), ритм задаём сами.

ВАЖНО для службы: в описании launchd обязателен ProcessType=Interactive, иначе macOS
склеивает таймеры фонового процесса и сон в 16 мс растягивается до 68 — ритм уезжает
вчетверо. Замерено.

Формат задания: {"seq": [шаг, ...]}. Шаги:
  {"op":"move",     "pts":[[x,y],...]}          точки в ЭКРАННЫХ координатах
  {"op":"click",    "button":"left|right", "count":1}
  {"op":"down"} / {"op":"up"}                   раздельные нажатие и отпускание
  {"op":"drag",     "pts":[[x,y],...]}          протянуть с зажатой кнопкой
  {"op":"scroll",   "dy":300, "dx":0}
  {"op":"type",     "text":"..."}
  {"op":"key",      "name":"enter", "mods":["cmd"], "count":1}
  {"op":"pause",    "ms":400}
"""
import glob
import json
import math
import os
import random
import time

import Quartz

TMP = os.path.expanduser("~/.dev-browser/tmp")
# Задания приходят отдельными файлами osin-<номер>.json, ответ кладётся рядом
# как osin-<номер>.done. Общий файл на все сессии не годится: две сессии затирают
# задания друг друга, а обе видят одну смену метки и считают свой ввод прошедшим.
TASK_GLOB = os.path.join(TMP, "osin-*.json")
LOG = os.path.join(TMP, "osin.log")

FRAME = 0.01667
OVERHEAD = 0.0034  # цена одной отправки, замерена: без вычета шаг уезжает с 16,7 на 20 мс

# Коды клавиш macOS. Нужны для служебных клавиш: их нельзя послать как символ.
KEYS = {
    "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51,
    "delete": 117, "escape": 53, "esc": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "a": 0, "c": 8, "v": 9, "x": 7, "z": 6, "f": 3, "l": 37, "r": 15, "t": 17, "w": 13,
    # q нужна для блокировки экрана сочетанием ctrl+cmd+q — иначе запереть мак изнутри
    # сессии нечем: из ssh это делать нельзя, система такой ввод не принимает.
    "q": 12,
}
MODS = {
    "cmd": Quartz.kCGEventFlagMaskCommand,
    "shift": Quartz.kCGEventFlagMaskShift,
    "alt": Quartz.kCGEventFlagMaskAlternate,
    "option": Quartz.kCGEventFlagMaskAlternate,
    "ctrl": Quartz.kCGEventFlagMaskControl,
}

BUTTONS = {
    "left": (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp,
             Quartz.kCGEventLeftMouseDragged, Quartz.kCGMouseButtonLeft),
    "right": (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp,
              Quartz.kCGEventRightMouseDragged, Quartz.kCGMouseButtonRight),
}

SHIFT_CODE = 56

# Раскладка US ANSI: символ → код клавиши. Настоящий код нужен, чтобы страница видела
# ту же пару code/key, что при живом наборе, а заглавные и знаки приходили с настоящим
# shift, а не подставленной строкой.
_KEYCAPS = [
    ("a", 0), ("s", 1), ("d", 2), ("f", 3), ("h", 4), ("g", 5), ("z", 6), ("x", 7),
    ("c", 8), ("v", 9), ("b", 11), ("q", 12), ("w", 13), ("e", 14), ("r", 15),
    ("y", 16), ("t", 17), ("1", 18), ("2", 19), ("3", 20), ("4", 21), ("6", 22),
    ("5", 23), ("=", 24), ("9", 25), ("7", 26), ("-", 27), ("8", 28), ("0", 29),
    ("]", 30), ("o", 31), ("u", 32), ("[", 33), ("i", 34), ("p", 35), ("l", 37),
    ("j", 38), ("'", 39), ("k", 40), (";", 41), ("\\", 42), (",", 43), ("/", 44),
    ("n", 45), ("m", 46), (".", 47), ("`", 50), (" ", 49),
]
_UPPER_ROW = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
    "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\",
    ":": ";", '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}
LAYOUT = {}
for _ch, _code in _KEYCAPS:
    LAYOUT[_ch] = (_code, False)
    if _ch.isalpha():
        LAYOUT[_ch.upper()] = (_code, True)
for _ch, _base in _UPPER_ROW.items():
    LAYOUT[_ch] = (LAYOUT[_base][0], True)

# Русская раскладка ЙЦУКЕН поверх тех же физических клавиш. Саму букву страница получает
# юникодной строкой, а код клавиши берём отсюда: иначе вся кириллица уходит с кодом одной
# клавиши, и это видно странице — живой набор так не выглядит.
_RU_ON_US = {
    "й": "q", "ц": "w", "у": "e", "к": "r", "е": "t", "н": "y", "г": "u", "ш": "i",
    "щ": "o", "з": "p", "х": "[", "ъ": "]",
    "ф": "a", "ы": "s", "в": "d", "а": "f", "п": "g", "р": "h", "о": "j", "л": "k",
    "д": "l", "ж": ";", "э": "'",
    "я": "z", "ч": "x", "с": "c", "м": "v", "и": "b", "т": "n", "ь": "m", "б": ",",
    "ю": ".", "ё": "`",
}
CYRILLIC = {}
for _ru, _us in _RU_ON_US.items():
    _code = LAYOUT[_us][0]
    CYRILLIC[_ru] = (_code, False)
    CYRILLIC[_ru.upper()] = (_code, True)

# Собственный источник событий. Без него событие наследует настоящее состояние
# модификаторов, и залипший cmd превращает набор в череду горячих клавиш.
SRC = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)


# Доли пропущенных кадров сняты с живого ввода Влада: 426 движений, 87% через один кадр,
# у остального длинный хвост. Ровный шаг в один кадр выдаёт скрипт.
def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def frames():
    r = random.random()
    if r < 0.014:
        return 0
    if r < 0.880:
        return 1
    if r < 0.929:
        return 2
    if r < 0.941:
        return 3
    if r < 0.948:
        return 4
    if r < 0.950:
        return 5
    return 6 + int(random.random() ** 3 * 55)


def lognorm(median, sigma):
    return max(0.008, median * random.lognormvariate(0, sigma))


def nap(seconds):
    time.sleep(max(0.001, seconds))


def here():
    e = Quartz.CGEventCreate(None)
    p = Quartz.CGEventGetLocation(e)
    return (p.x, p.y)


def mouse(kind, pt, button=Quartz.kCGMouseButtonLeft, clicks=1):
    ev = Quartz.CGEventCreateMouseEvent(None, kind, pt, button)
    if clicks > 1:
        Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, clicks)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def do_move(pts, kind=Quartz.kCGEventMouseMoved, button=Quartz.kCGMouseButtonLeft):
    # Негодную точку пропускаем с записью в журнал: одна кривая координата не должна
    # ронять весь жест, но и молча теряться тоже не должна.
    good = []
    for p in pts:
        try:
            good.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError, IndexError):
            log("точка пропущена: %r" % (p,))
    pts = good
    for x, y in pts:
        mouse(kind, (float(x), float(y)), button)
        # Иногда рука замирает, но кадр всё равно приносит событие с тем же местом.
        if random.random() < 0.05:
            nap(FRAME - OVERHEAD)
            mouse(kind, (float(x), float(y)), button)
        nap(frames() * FRAME - OVERHEAD)


def do_click(button="left", count=1):
    down, up, _, btn = BUTTONS.get(button, BUTTONS["left"])
    p = here()
    nap(lognorm(0.12, 0.5))
    for n in range(1, count + 1):
        mouse(down, p, btn, n)
        nap(lognorm(0.07, 0.4))
        mouse(up, p, btn, n)
        if n < count:
            nap(lognorm(0.09, 0.3))  # интервал внутри двойного клика
    nap(lognorm(0.30, 0.5))


def do_press(button="left"):
    down, _, _, btn = BUTTONS.get(button, BUTTONS["left"])
    nap(lognorm(0.10, 0.5))
    mouse(down, here(), btn)
    nap(lognorm(0.08, 0.4))


def do_release(button="left"):
    _, up, _, btn = BUTTONS.get(button, BUTTONS["left"])
    nap(lognorm(0.08, 0.4))
    mouse(up, here(), btn)
    nap(lognorm(0.25, 0.5))


def do_drag(pts, button="left"):
    down, up, dragged, btn = BUTTONS.get(button, BUTTONS["left"])
    if not pts:
        return
    start = (float(pts[0][0]), float(pts[0][1]))
    mouse(Quartz.kCGEventMouseMoved, start, btn)
    nap(lognorm(0.15, 0.4))
    mouse(down, start, btn)
    nap(lognorm(0.09, 0.4))
    do_move(pts[1:], dragged, btn)
    nap(lognorm(0.13, 0.4))
    mouse(up, (float(pts[-1][0]), float(pts[-1][1])), btn)
    nap(lognorm(0.30, 0.5))


def do_scroll(dy=0, dx=0):
    # Трекпадная волна: разгон, полка, затухание. Один кусок у человека не встречается.
    for axis, total in (("y", int(dy)), ("x", int(dx))):
        if not total:
            continue
        direction = 1 if total > 0 else -1
        left = abs(total)
        while left > 2:
            peak = min(left, int(14 + random.random() * 10))
            n = max(3, min(24, int(left / max(1, peak) * (1.6 + random.random()))))
            for i in range(n):
                if left <= 0:
                    break
                phase = (i + 0.5) / n
                env = math.sin(math.pi * phase ** 0.75)
                tick = max(1, min(left, int(peak * env * (0.8 + random.random() * 0.4))))
                if axis == "y":
                    ev = Quartz.CGEventCreateScrollWheelEvent(
                        None, Quartz.kCGScrollEventUnitPixel, 1, -direction * tick)
                else:
                    ev = Quartz.CGEventCreateScrollWheelEvent(
                        None, Quartz.kCGScrollEventUnitPixel, 2, 0, -direction * tick)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
                left -= tick
                nap(FRAME * (1 if random.random() < 0.85 else 2))
            if left > 2:
                nap(lognorm(0.18, 0.5))
    nap(lognorm(0.42, 0.5))


def _tap(code, down, flags=0, text=None):
    ev = Quartz.CGEventCreateKeyboardEvent(SRC, code, down)
    Quartz.CGEventSetFlags(ev, flags)  # выставляем всегда, в том числе ноль
    if text is not None:
        Quartz.CGEventKeyboardSetUnicodeString(ev, len(text), text)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


def _char(ch):
    """Один символ настоящей клавишей.

    Латиница, цифры и знаки набираются кодом клавиши из раскладки, заглавные и верхний
    ряд — с настоящим зажатым shift. Кириллица приходит юникодной строкой на событии с
    кодом своей клавиши по ЙЦУКЕН: Chromium делает из строки keydown и вставку текста,
    а код клавиши остаётся тем же, что при живом наборе на русской раскладке.
    """
    known = LAYOUT.get(ch)
    if known:
        code, need_shift = known
        text = None
    elif ch in CYRILLIC:
        code, need_shift = CYRILLIC[ch]
        text = ch
    else:
        code, need_shift, text = 0, False, ch
    flags = Quartz.kCGEventFlagMaskShift if need_shift else 0

    if need_shift:
        _tap(SHIFT_CODE, True, flags)
        nap(lognorm(0.035, 0.3))
    _tap(code, True, flags, text)
    nap(lognorm(0.055, 0.35))  # клавиша под пальцем
    _tap(code, False, flags, text)
    if need_shift:
        nap(lognorm(0.03, 0.3))
        _tap(SHIFT_CODE, False, 0)


def do_type(text):
    """Набор посимвольно, в ритме живой руки.

    Пауза между клавишами логнормальная около 105 мс, пробел чуть длиннее, два
    одинаковых символа подряд — медленнее (один палец), изредка длинная пауза-заминка.
    """
    nap(lognorm(0.22, 0.4))  # рука доходит до клавиатуры
    prev = ""
    for ch in text:
        _char(ch)
        pause = lognorm(0.105, 0.45)
        if ch == " ":
            pause *= 1.3
        if prev and prev.lower() == ch.lower():
            pause *= 1.4
        if random.random() < 0.04:
            pause += lognorm(0.32, 0.5)  # заминка: нашёл клавишу не сразу
        nap(pause)
        prev = ch
    nap(lognorm(0.35, 0.4))


def do_key(name, mods=None, count=1):
    code = KEYS.get(str(name).lower())
    if code is None:
        raise ValueError("неизвестная клавиша: %s" % name)
    flags = 0
    for m in (mods or []):
        flags |= MODS.get(str(m).lower(), 0)
    for _ in range(count):
        _tap(code, True, flags)
        nap(lognorm(0.04, 0.3))
        _tap(code, False, flags)
        nap(lognorm(0.12, 0.4))


HANDLERS = {
    "move": lambda s: do_move(s.get("pts", [])),
    "click": lambda s: do_click(s.get("button", "left"), int(s.get("count", 1))),
    "down": lambda s: do_press(s.get("button", "left")),
    "up": lambda s: do_release(s.get("button", "left")),
    "drag": lambda s: do_drag(s.get("pts", []), s.get("button", "left")),
    "scroll": lambda s: do_scroll(s.get("dy", 0), s.get("dx", 0)),
    "type": lambda s: do_type(s.get("text", "")),
    "key": lambda s: do_key(s.get("name"), s.get("mods"), int(s.get("count", 1))),
    "pause": lambda s: nap(s.get("ms", 100) / 1000.0),
}


os.makedirs(TMP, exist_ok=True)
log("демон поднят, операций в словаре: %d" % len(HANDLERS))

while True:
    задания = sorted(glob.glob(TASK_GLOB), key=os.path.getmtime)
    if not задания:
        time.sleep(0.02)
        continue
    путь = задания[0]
    try:
        with open(путь) as f:
            task = json.load(f)
        os.remove(путь)
    except Exception as exc:
        log("задание не прочиталось: %s" % exc)
        try:
            os.remove(путь)
        except Exception:
            pass
        continue

    err = ""
    try:
        for step in task.get("seq", []):
            handler = HANDLERS.get(step.get("op"))
            if handler is None:
                raise ValueError("неизвестная операция: %s" % step.get("op"))
            handler(step)
        log("шагов выполнено: %d" % len(task.get("seq", [])))
    except Exception as exc:
        err = str(exc)
        log("ошибка: %s" % err)

    ответ = путь[:-5] + ".done"
    with open(ответ, "w") as f:
        f.write(json.dumps({"t": time.time(), "err": err}))
