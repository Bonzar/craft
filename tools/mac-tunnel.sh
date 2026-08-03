#!/usr/bin/env bash
# Поднимает выход в интернет через MacBook Влада, объявленный exit node в Tailscale.
# Нужен там, где сайт режет облачные адреса: s7.ru за антиботом ServicePipe отдаёт 403
# настоящему Chrome с подменой отпечатка с любого хостингового IP, а с домашнего — 200.
# Полное описание контура, условия на стороне ноута и диагностика — в CLAUDE.md,
# подраздел «Выход через MacBook Влада».
#
# Запускается ВРУЧНУЮ, когда задача требует домашнего выхода. В settings.json намеренно
# не регистрируется: канал нужен редко, а на старте сессии ноут может спать. Живёт вне
# .claude/hooks, потому что тамошняя обратная проверка тестов требует регистрации каждого
# файла и уронила бы прогон на незарегистрированном помощнике.
#
# Идемпотентен: повторный запуск переиспользует живой демон и лишь пере-проверяет выход.
#
# Usage: bash tools/mac-tunnel.sh
#   exit 0 — канал поднят, напечатан маскированный адрес выхода и адрес прокси
#   exit 1 — канал недоступен, причина названа
set -u

# --- константы: единственный источник значений, документация ссылается сюда ------------
TS_VERSION="1.80.3"                       # пин, не latest: урок Xray в CLAUDE.md
TS_ARCH="amd64"
MAC_NODE="100.87.74.4"                    # tailnet-адрес MacBook; проверен именно адрес,
                                          # CLI принимает и имя, но тот путь не исполнялся
SOCKS_PORT="1055"
WORKDIR="${MAC_TUNNEL_DIR:-$HOME/.local/share/mac-tunnel}"
PIDFILE="$WORKDIR/tailscaled.pid"
STATEFILE="$WORKDIR/ts.state"
LOGFILE="$WORKDIR/tailscaled.log"

log(){ echo "[mac-tunnel] $*" >&2; }
die(){ log "$*"; exit 1; }

# Env репозитория: TAILSCALE_AUTHKEY кладётся в настройки окружения, Claude Code сам .env
# не читает. Хелпер лежит в хуках; отсутствует — не фатально, переменная может быть в env.
_env_helper="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.claude/hooks/_load-env.sh"
[[ -f "$_env_helper" ]] && . "$_env_helper"

[[ -n "${TAILSCALE_AUTHKEY:-}" ]] || die "нет TAILSCALE_AUTHKEY в окружении — ключ выпускается в веб-панели Tailscale и кладётся в настройки облачного окружения"

mkdir -p "$WORKDIR" || die "не создать $WORKDIR"

# --- 1. бинарники ----------------------------------------------------------------------
if [[ ! -x "$WORKDIR/tailscale" || ! -x "$WORKDIR/tailscaled" ]]; then
  log "качаю tailscale $TS_VERSION"
  tgz="$WORKDIR/ts.tgz"
  curl -sSL -m 300 -o "$tgz" \
    "https://pkgs.tailscale.com/stable/tailscale_${TS_VERSION}_${TS_ARCH}.tgz" \
    || die "не скачать tailscale $TS_VERSION"
  # Внутри архива всё лежит в каталоге tailscale_<ver>_<arch>/ — его и срезаем.
  tar xzf "$tgz" --strip-components=1 -C "$WORKDIR" \
    "tailscale_${TS_VERSION}_${TS_ARCH}/tailscale" \
    "tailscale_${TS_VERSION}_${TS_ARCH}/tailscaled" \
    || die "не распаковать архив tailscale"
  chmod +x "$WORKDIR/tailscale" "$WORKDIR/tailscaled"
fi
TS="$WORKDIR/tailscale"

# --- 2. демон --------------------------------------------------------------------------
# Перезапуск строго по pid-файлу. Поиск процесса по текстовому шаблону запрещён: в CLAUDE.md
# зафиксировано, что pkill -f убивает и собственную bash-команду.
daemon_alive() {
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null)" || return 1
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

if ! daemon_alive; then
  log "поднимаю демон (userspace, socks5 на :$SOCKS_PORT)"
  # HTTPS_PROXY демону НЕ снимаем: он ходит к controlplane и реле через агент-прокси.
  nohup "$WORKDIR/tailscaled" \
    --tun=userspace-networking \
    --socks5-server="127.0.0.1:$SOCKS_PORT" \
    --state="$STATEFILE" >"$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  for _ in $(seq 1 30); do
    [[ -S /var/run/tailscale/tailscaled.sock ]] && break
    sleep 1  # timed-wait: ждём появления управляющего сокета
  done
  [[ -S /var/run/tailscale/tailscaled.sock ]] || die "демон не поднялся, смотри $LOGFILE"
fi

# --- 3. вход в сеть и выбор выходного узла ----------------------------------------------
# Ключ в вывод не печатается. up может не применить exit-node, поэтому следом идёт set.
"$TS" up --authkey="$TAILSCALE_AUTHKEY" --hostname=claude-cloud \
        --exit-node="$MAC_NODE" --exit-node-allow-lan-access=false \
        --accept-routes >/dev/null 2>&1
up_rc=$?
"$TS" set --exit-node="$MAC_NODE" >/dev/null 2>&1

status="$("$TS" status 2>/dev/null)"
if ! grep -q "$MAC_NODE" <<<"$status"; then
  if (( up_rc != 0 )); then
    die "узла нет в сети и вход не прошёл — вероятно ключ не принят или просрочен"
  fi
  die "узла $MAC_NODE нет в сети — ноут спит или выключен"
fi

# --- 4. проверка выхода ------------------------------------------------------------------
# Признак успеха — адрес через прокси ОТЛИЧАЕТСЯ от адреса контейнера. Домашний адрес не
# зашивается: он динамический. ip-api.com не берём — у него лимит на исходящий адрес.
noproxy_env=(env -u HTTPS_PROXY -u https_proxy -u NO_PROXY -u no_proxy)
fetch_ip() {  # $1 — доп. аргументы curl
  local svc
  for svc in https://checkip.amazonaws.com https://api.ipify.org; do
    out="$("${noproxy_env[@]}" curl -sS -m 45 $1 "$svc" 2>/dev/null | tr -d '[:space:]')"
    [[ "$out" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] && { echo "$out"; return 0; }
  done
  return 1
}

direct_ip="$(curl -sS -m 45 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]')"

# Разрыв реле — не отказ канала: повторяем.
proxy_ip=""
for _ in $(seq 1 6); do
  if proxy_ip="$(fetch_ip "-x socks5://127.0.0.1:$SOCKS_PORT")"; then break; fi
  proxy_ip=""
  sleep 4  # timed-wait: пауза между попытками сквозь реле
done

[[ -n "$proxy_ip" ]] || die "выход не отвечает: узел в сети, но канал молчит — смотри счётчики в «$TS status»"

if [[ -n "$direct_ip" && "$proxy_ip" == "$direct_ip" ]]; then
  die "адрес через прокси совпал с адресом контейнера — ноут доступен, но не маршрутизирует (проверь пересылку пакетов на нём)"
fi

mask="$(cut -d. -f1,2 <<<"$proxy_ip").x.x"
log "выход через ноут: $mask"
log "прокси: socks5://127.0.0.1:$SOCKS_PORT (схема socks5, НЕ socks5h)"
exit 0
