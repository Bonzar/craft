#!/bin/bash
# Уборка браузеров агента: вкладки копятся за сессию, а лишние профили остаются висеть
# после опытов. Без уборки мак постепенно забивается окнами и памятью.
#
# Что делает:
#  1. В постоянных профилях оставляет по одной вкладке, остальные закрывает. Страница
#     проверки-капчи неприкосновенна — её как раз и решает Влад с пульта.
#  2. Профили вне постоянного списка гасит целиком.
#
# Защита от уборки посреди работы: сессия трогает файл-метку /tmp/agchrome-keep.<порт>,
# и профиль с меткой свежее получаса не трогается вовсе.
#
# Вкладками рулим через http-ручки отладки (/json/list, /json/close) — без websocket и
# без сторонних модулей. Процессы гасим ТОЛЬКО по найденному номеру, не по маске.
#
# Имена переменных латиницей: на маке системный bash версии 3.2, и кириллическое имя он
# за имя не считает — «оставить=0: command not found». Замерено на живом прогоне.
set -u
KEEP_PORTS="9333 9303"
KEEP_DIRS="agprof agprof-pult1"
MARK_AGE_MIN=30

busy() {  # порт → есть ли свежая метка занятости
  f="/tmp/agchrome-keep.$1"
  [ -f "$f" ] || return 1
  [ -n "$(find "$f" -mmin -$MARK_AGE_MIN 2>/dev/null)" ]
}

for port in $KEEP_PORTS; do
  if busy "$port"; then echo "$port занят, не трогаю"; continue; fi
  # Список вкладок отдаём питону через стандартный ввод: json большой и в аргумент
  # командной строки лезть не должен.
  ids="$(curl -s --max-time 5 "http://127.0.0.1:$port/json/list" 2>/dev/null | /usr/bin/python3 -c '
import json, sys
try:
    tabs = [t for t in json.load(sys.stdin) if t.get("type") == "page"]
except Exception:
    sys.exit(0)
keep = set()
if tabs:
    keep.add(tabs[0]["id"])
for t in tabs:
    u = t.get("url", "")
    if "xpvnsulc" in u or "captcha" in u:
        keep.add(t["id"])
print(" ".join(t["id"] for t in tabs if t["id"] not in keep))
' 2>/dev/null)"
  n=0
  for id in $ids; do
    curl -s --max-time 4 "http://127.0.0.1:$port/json/close/$id" >/dev/null 2>&1 && n=$((n+1))
  done
  echo "$port: закрыто лишних вкладок — $n"
done

# Профили вне списка гасим целиком, по номеру процесса. Берём только головной процесс
# Chrome: у него в командной строке есть --user-data-dir и нет --type=.
for pid in $(pgrep -f "user-data-dir=/Users/agent/agprof" 2>/dev/null); do
  cmd="$(ps -o command= -p "$pid" 2>/dev/null)"
  case "$cmd" in *--type=*) continue ;; esac
  dir="$(echo "$cmd" | sed -n 's/.*user-data-dir=\/Users\/agent\/\([^ ]*\).*/\1/p')"
  [ -n "$dir" ] || continue
  keep=0
  for k in $KEEP_DIRS; do
    [ "$dir" = "$k" ] && keep=1
  done
  if [ "$keep" -eq 0 ]; then
    echo "гашу лишний профиль $dir (pid $pid)"
    kill "$pid" 2>/dev/null
  fi
done
echo "уборка закончена"
