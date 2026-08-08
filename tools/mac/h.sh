#!/usr/bin/env bash
# Пульт с человекоподобным вводом: склеивает слой траекторий, слой ввода и рабочий
# скрипт в одну программу и шлёт её dev-browser на маке.
#
# Профиль браузера и порт задаются переменными: DB_PORT (по умолчанию рабочий 9333).
# Профиль у сессий ОБЩИЙ — он копит пропуска антиботов и логины; чистые профили заводим
# только под замеры.
#
# Заодно ретранслирует зов о помощи. Сетевого вызова в песочнице нет, поэтому сессия
# кричит строкой «ЗАСТРЯЛА <идентификатор вкладки> <адрес> :: <повод>», а мы заявляем эту
# вкладку в пульт капчи и печатаем готовую ссылку для Влада. Заявку снимает строка
# «ПРОШЛА <идентификатор вкладки> <адрес>».
#
# Usage: bash h.sh script.js [таймаут-сек]
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GC="$DIR/gc.js"
T="${2:-120}"
PORT="${DB_PORT:-9333}"
CM="${DB_SSH_CTL:-/tmp/sshcm}"
GATE="${CAPGATE_URL:-http://127.0.0.1:8899}"
mkdir -p "$CM"
OPTS=(-o "ProxyCommand=nc -X 5 -x 127.0.0.1:1055 %h %p"
      -o "ControlMaster=auto" -o "ControlPath=$CM/%r@%h:%p" -o "ControlPersist=10m"
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
      -o LogLevel=ERROR -o ConnectTimeout=25)
# Адрес узла — из окружения: в репозитории его нет, это персональные данные Влада.
_env_helper="$(cd "$DIR/../.." && pwd)/.claude/hooks/_load-env.sh"
[[ -f "$_env_helper" ]] && . "$_env_helper"
[[ -n "${MAC_NODE_ADDR:-}" ]] || { echo "нет MAC_NODE_ADDR в окружении — tailnet-адрес мака кладётся в настройки облачного окружения"; exit 1; }
HOST="agent@$MAC_NODE_ADDR"

[[ -f "$GC" ]] || { echo "нет слоя траекторий: $GC"; exit 1; }

# Метка занятости профиля: уборка не трогает то, с чем сейчас работают. Ставится не
# однажды, а бьётся сердцем всё время прогона — иначе работа дольше окна уборки переставала
# защищать вкладки, и они закрывались прямо под сессией. Сердце живёт на маке рядом с
# прогоном и умирает вместе с ним.
ssh "${OPTS[@]}" "$HOST" "touch /tmp/agchrome-keep.$PORT" >/dev/null 2>&1 || true

# Ключ -n обязателен: без него вложенный ssh читает тот же stdin, что и цикл разбора вывода,
# и съедает остаток потока — вторая заявка прогона просто пропадала.
gate() {  # $1 — json тела заявки
  ssh -n "${OPTS[@]}" "$HOST" \
    "curl -sS --max-time 12 -X POST -H 'content-type: application/json' -d '$1' $GATE/register" 2>/dev/null
}

# Сердце метки: фоновый цикл на маке трогает её раз в полминуты и сам умирает, когда
# кончается прогон (kill -0 по родительской оболочке). Продлевать метку из контейнера
# нельзя — каждый удар стоил бы отдельного захода через реле.
СЕРДЦЕ="( while kill -0 \$\$ 2>/dev/null; do touch /tmp/agchrome-keep.$PORT; sleep 30; done ) >/dev/null 2>&1 &"

cat "$GC" "$DIR/prelude.js" "$1" \
  | ssh "${OPTS[@]}" "$HOST" \
      "export PATH=\$HOME/.npm-global/bin:\$PATH; $СЕРДЦЕ dev-browser --connect http://127.0.0.1:$PORT --timeout $T" \
  | while IFS= read -r line; do
      printf '%s\n' "$line"
      case "$line" in
        "ЗАСТРЯЛА "*)
          rest="${line#ЗАСТРЯЛА }"
          tgt="${rest%% *}"          # идентификатор вкладки идёт первым: адрес бывает с пробелами
          rest="${rest#* }"
          url="${rest%% :: *}"
          why="${rest#* :: }"
          out="$(gate "{\"цель\":\"$tgt\",\"адрес\":\"$url\",\"повод\":\"$why\"}")"
          echo "ЧЕЛОВЕК НУЖЕН: $out"
          ;;
        "ПРОШЛА "*)
          rest="${line#ПРОШЛА }"
          tgt="${rest%% *}"
          url="${rest#* }"
          gate "{\"цель\":\"$tgt\",\"адрес\":\"$url\",\"снять\":true}" >/dev/null
          echo "заявка снята"
          ;;
      esac
    done
