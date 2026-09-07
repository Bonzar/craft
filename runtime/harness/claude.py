"""Таблица харнеса Claude Code: имена его событий и полей, факты обёртки, рендер
решения в его формат.

ЗДЕСЬ и только здесь живут имена полей этого харнеса, имена его событий и его
переменные окружения: логика модуля (`decide.py`) не видит ничего из этого — всё
приходит к ней данными. Таблиц харнесов будет три (claude, codex, aisuite); эта
первая, и она единственное место, куда придётся смотреть, когда добавится вторая.

Здесь же — то, КУДА и В КАКОЙ ФОРМЕ пишется регистрация этого харнеса: путь его
настроек и вид записи в них. Без этого знание о харнесе разъезжалось бы на два
файла, и вторая таблица потребовала бы переписывать установщик, а не добавлять
строку.

Отдаёт КАНОНИЧЕСКОЕ событие (то же ядро, что .claude/hooks/lib/event.js):
harness, session_id, call_id, event, tool, input, cwd, state_dir — и сверх ядра
два ФАКТА ОБЁРТКИ, которых харнес не даёт, а считает она:
  key          — ключ следа (pylib/key.py);
  decision_log — путь журнала решений, куда модуль кладёт след.

Пути состояния сюда не входят: про харнес в них ничего нет, и живут они в
pylib/state.py — иначе каждая новая таблица копировала бы их заново.

Fail open на всём неожиданном: пустое или неразборное событие даёт пустые поля,
а не падение.
"""

import json
import os

from key import event_key
from state import decision_log, state_dir

HARNESS = "claude"

# Имена событий харнеса → канонические. Единственное место, где имена событий
# Claude Code вообще упоминаются.
EVENT_BY_HARNESS = {
    "SessionStart": "session-start",
    "UserPromptSubmit": "prompt",
    "PreToolUse": "pre-tool",
    "PostToolUse": "post-tool",
    "PostToolUseFailure": "post-tool-failure",
    "Stop": "stop",
    "SubagentStop": "subagent-stop",
    "PreCompact": "pre-compact",
    "SessionEnd": "session-end",
    "Notification": "notification",
}
HARNESS_BY_EVENT = {v: k for k, v in EVENT_BY_HARNESS.items()}


# Какой исход харнес принимает на каком событии. Подделывать имя события нельзя:
# харнес сверяет его с тем, на которое подписан модуль, и чужую форму молча
# выбрасывает — решение исчезло бы, а след при этом уже лёг.
ACCEPTS = {
    "pre-tool": ("allow", "ask", "deny"),
    "stop": ("block",),
    # `subagent-stop` харнес блокировать умеет, но записи метрик у этого события
    # нет (universal-metrics.js пишет только STOP), и сшивать след было бы не с
    # чем: блокировка легла бы в журнал и не досчиталась молча. Появится
    # запись — вернуть строку сюда.
}


# ФАКТЫ сверх ядра, которые эта таблица считает и кладёт в событие. Пусто — и это
# УТВЕРЖДЕНИЕ, а не пропуск: `journal` и `tokens` (lib/event.js) она пока не
# выдаёт, поэтому модуль, объявивший их в `requires`, не ставится вовсе — вместо
# того чтобы встать и отвечать `unsupported` на каждом событии.
FACTS = ()


# События, на которых харнес принимает ПРАВКУ ВХОДА. Спрашивают это у таблицы, а
# не ищут маркер в её выводе: причина решения со словом `updatedInput` внутри
# погасила бы предупреждение о потерянной правке.
ACCEPTS_INPUT = ("pre-tool",)


def accepts_input(event_name):
    return event_name in ACCEPTS_INPUT


def events():
    """Канонические имена событий, которые этот харнес умеет присылать."""
    return sorted(set(EVENT_BY_HARNESS.values()))


def settings_path():
    """Файл настроек харнеса, куда пишется строка регистрации."""
    return os.path.join(os.path.expanduser("~"), ".claude", "settings.json")


def registration(command):
    """Запись регистрации в формате настроек этого харнеса."""
    return {"type": "command", "command": command}


def harness_event(name):
    """Каноническое имя события → имя харнеса. Пусто, если такого нет: харнес
    сверяет имя в ответе с тем событием, на которое подписан модуль."""
    return HARNESS_BY_EVENT.get(name, "")


def to_event(raw):
    """Сырые байты со stdin → каноническое событие."""
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw and raw.strip() else {}
    except (ValueError, UnicodeDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    def text(name):
        value = parsed.get(name)
        return value if isinstance(value, str) else ""

    session_id = text("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    call_id = text("tool_use_id")
    tool_input = parsed.get("tool_input")
    directory = state_dir()
    return {
        "harness": HARNESS,
        "session_id": session_id,
        "call_id": call_id,
        # Неизвестное имя события даёт ПУСТОЕ имя, а не догадку: модуль,
        # подписанный на своё событие, тогда просто не сработает, тогда как
        # догадка запустила бы его не на том событии.
        "event": EVENT_BY_HARNESS.get(text("hook_event_name"), ""),
        "tool": text("tool_name"),
        "input": tool_input if isinstance(tool_input, dict) else {},
        "cwd": text("cwd"),
        "state_dir": directory,
        "key": event_key(call_id, raw or b""),
        "decision_log": decision_log(session_id, directory),
    }


def render(event, decision):
    """Решение → то, что печатается харнесу. Пустая строка значит «печатать
    нечего»: молчание и есть проход."""
    decision = decision or {}
    outcome = decision.get("outcome") or ""
    reason = decision.get("reason") or ""
    name = harness_event((event or {}).get("event") or "")

    if outcome == "block":
        if outcome not in ACCEPTS.get((event or {}).get("event") or "", ()):
            return ""
        return json.dumps({"decision": "block", "reason": decision.get("message") or reason})
    if outcome in ("deny", "ask", "allow"):
        # Формы нет — печатать нечего. Подставить сюда имя события, на котором
        # харнес такого исхода не принимает, значило бы отдать ему ответ, который
        # он молча выбросит, а сводка посчитала бы отказ, которого не было.
        if outcome not in ACCEPTS.get((event or {}).get("event") or "", ()):
            return ""
        out = {
            "hookSpecificOutput": {
                "hookEventName": name,
                "permissionDecision": outcome,
                "permissionDecisionReason": reason,
            }
        }
        if isinstance(decision.get("modified_input"), dict):
            out["hookSpecificOutput"]["updatedInput"] = decision["modified_input"]
        return json.dumps(out)
    # `none` — «я не решал». Дописанный контекст при этом всё же печатается: он
    # не исключает чужого решения и не обрывает ничью цепочку.
    context = decision.get("add_context")
    if outcome == "none" and context and name:
        return json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}})
    return ""
