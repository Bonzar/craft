#!/bin/bash
# Поднимает постоянные браузеры агента. Идемпотентен: профиль, чей порт уже отвечает,
# пропускается — значит скрипт можно звать сколько угодно, в том числе из launchd при
# входе в систему и вручную после сбоя.
#
# Профиль = пара «порт : каталог». Прогретый (agprof) несёт заработанные пропуска
# антиботов и переживает перезагрузку; профиль пульта отдельный, чтобы уборка и опыты
# не задевали рабочий.
set -u
PROFILES="9333:agprof 9303:agprof-pult1"
CHROME="/Applications/Google Chrome.app"

for pair in $PROFILES; do
  port="${pair%%:*}"
  dir="${pair##*:}"
  if curl -s --max-time 2 "http://127.0.0.1:$port/json/version" >/dev/null 2>&1; then
    echo "$port уже поднят ($dir)"
    continue
  fi
  echo "поднимаю $port ($dir)"
  open -na "$CHROME" --args \
    --remote-debugging-port="$port" \
    --user-data-dir="/Users/agent/$dir" \
    --no-first-run --no-default-browser-check --window-size=1440,900
  sleep 6
done

for pair in $PROFILES; do
  port="${pair%%:*}"
  if curl -s --max-time 3 "http://127.0.0.1:$port/json/version" >/dev/null 2>&1; then
    echo "$port готов"
  else
    echo "$port НЕ поднялся"
  fi
done
