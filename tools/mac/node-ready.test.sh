#!/usr/bin/env bash
# Тест ожидания готовности узла в сети.
#
# Запуск: bash tools/mac/node-ready.test.sh
# Ни сети, ни мака не нужно — клиент Tailscale подменяется заглушкой.
#
# Кейсы названы по трём дефектам, из-за которых скрипт канала объявлял «узла нет в сети» на
# исправном контуре: однократный взгляд вместо ожидания; поиск по полному доменному имени,
# которого в списке нет; и он же переданный клиенту выходным узлом, отчего вход не выполнялся
# вовсе, а виноватым назначался ключ.
set -u
cd "$(dirname "$0")/../.." || exit 1
export LC_ALL=C.UTF-8

READY="tools/mac/node-ready.sh"
FULL="bonzarr.taile5403c.ts.net"
IP="100.87.74.4"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/stub"

ok=0
fail=0

# Заглушка клиента: печатает машинное состояние по сценарию. Счётчик опросов лежит файлом,
# чтобы сценарий «узел появляется не сразу» был воспроизводимым.
поставить() {  # $1 — сценарий
  : > "$WORK/calls"
  cat > "$WORK/stub/tailscale" <<STUB
#!/usr/bin/env bash
echo x >> "$WORK/calls"
n=\$(wc -l < "$WORK/calls")
ONLINE='{"BackendState":"Running","Peer":{"k":{"DNSName":"$FULL.","HostName":"MacBook-Air-Vladislav","TailscaleIPs":["$IP","fd7a:115c:a1e0::401:4ac2"],"Online":true,"ExitNodeOption":true}}}'
LOGGEDOUT='{"BackendState":"NeedsLogin","Peer":{}}'
EMPTY='{"BackendState":"Running","Peer":{}}'
OFFLINE='{"BackendState":"Running","Peer":{"k":{"DNSName":"$FULL.","HostName":"MacBook-Air-Vladislav","TailscaleIPs":["$IP"],"Online":false,"ExitNodeOption":true}}}'
case "$1" in
  сразу)        printf '%s' "\$ONLINE" ;;
  с-третьего)   if [ "\$n" -ge 3 ]; then printf '%s' "\$ONLINE"; else printf '%s' "\$EMPTY"; fi ;;
  вход-не-сразу) if [ "\$n" -ge 3 ]; then printf '%s' "\$ONLINE"; else printf '%s' "\$LOGGEDOUT"; fi ;;
  никогда)      printf '%s' "\$EMPTY" ;;
  спит)         printf '%s' "\$OFFLINE" ;;
  без-входа)    printf '%s' "\$LOGGEDOUT" ;;
esac
exit 0
STUB
  chmod +x "$WORK/stub/tailscale"
}

проба() {  # $1 — имя, $2 — сценарий, $3 — узел, $4 — ждём код 0? (да/нет), $5 — ждём вывод
  поставить "$2"
  local out rc want_rc=1
  out="$(NODE_READY_LIMIT=12 bash "$READY" "$WORK/stub/tailscale" "$3" 2>/dev/null)"; rc=$?
  [ "$4" = "да" ] && want_rc=0
  if [ "$rc" -ne "$want_rc" ]; then
    echo "FAIL   $1 — код $rc, ждали $want_rc (вывод: $out)"; fail=$((fail+1)); return
  fi
  if [ "$out" != "$5" ]; then
    echo "FAIL   $1 — вывод «$out», ждали «$5»"; fail=$((fail+1)); return
  fi
  echo "PASS   $1"; ok=$((ok+1))
}

проба "узел, появившийся не с первого опроса, считался отсутствующим" \
      "с-третьего" "$FULL" "да" "готов $IP"
проба "узел ищется по полному доменному имени, а не только по короткому" \
      "сразу" "$FULL" "да" "готов $IP"
проба "узел ищется и по короткому имени" \
      "сразу" "bonzarr" "да" "готов $IP"
проба "узел ищется и по адресу" \
      "сразу" "$IP" "да" "готов $IP"
проба "невыполненный вход — переходное состояние, а не приговор" \
      "вход-не-сразу" "$FULL" "да" "готов $IP"
проба "узла нет вовсе — отказ про узел" \
      "никогда" "$FULL" "нет" "нет-узла"
проба "узел не в сети — отказ про узел, а не про ключ" \
      "спит" "$FULL" "нет" "нет-узла"
проба "вход так и не выполнен — отказ про вход" \
      "без-входа" "$FULL" "нет" "нет-входа"

echo "---------------------------------------------------------------------------"
echo "NODE-READY: $ok/$((ok + fail)) passed; $fail failed"
[ "$fail" -eq 0 ]
