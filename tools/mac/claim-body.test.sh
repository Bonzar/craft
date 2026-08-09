#!/usr/bin/env bash
# Тест сборки тела заявки в пульт капчи.
#
# Запуск: bash tools/mac/claim-body.test.sh
# Ни мака, ни браузера не нужно.
#
# Первый кейс носит имя находки ревью безопасности: адрес страницы с одинарной кавычкой
# уезжал в текст удалённой команды и позволял сайту выполнить свои команды на маке от имени
# учётки агента. Адрес приходит с недоверенного сайта, и это штатный путь инструмента —
# зов о помощи случается ровно тогда, когда сайт заблокировал агента.
#
# Меряем ДВА свойства:
#   1) тело — валидный JSON, и поля в нём равны входу СИМВОЛ В СИМВОЛ (значит спецсимволы
#      остались данными, а не разметкой);
#   2) негодные поля заявку не выпускают вовсе.
set -u
cd "$(dirname "$0")/../.." || exit 1
export LC_ALL=C.UTF-8

BUILDER="tools/mac/claim-body.sh"
ok=0
fail=0

# Проверяет: тело разбирается как JSON и поля совпадают со входом дословно.
целость() {  # $1 — имя кейса, $2 — id, $3 — url, $4 — повод
  local name="$1" id="$2" url="$3" why="$4" body rc
  body="$(bash "$BUILDER" "$id" "$url" "$why" 2>/dev/null)"; rc=$?
  if [ $rc -ne 0 ]; then
    echo "FAIL   $name — сборка отказала, а вход законный"; fail=$((fail+1)); return
  fi
  if ID="$id" URL="$url" WHY="$why" BODY="$body" python3 -c '
import json, os, sys
try:
    d = json.loads(os.environ["BODY"])
except Exception as e:
    print("не JSON: " + str(e)); sys.exit(1)
if d.get("цель") != os.environ["ID"]:   print("цель искажена: " + repr(d.get("цель")));  sys.exit(1)
if d.get("адрес") != os.environ["URL"]: print("адрес искажён: " + repr(d.get("адрес"))); sys.exit(1)
if d.get("повод") != os.environ["WHY"]: print("повод искажён: " + repr(d.get("повод"))); sys.exit(1)
' 2>&1 | grep -q .; then
    echo "FAIL   $name"
    ID="$id" URL="$url" WHY="$why" BODY="$body" python3 -c '
import json, os
try:
    d = json.loads(os.environ["BODY"])
    print("       поля: " + repr(d))
except Exception as e:
    print("       тело не разбирается как JSON: " + str(e))
    print("       тело: " + os.environ["BODY"][:200])'
    fail=$((fail+1))
  else
    echo "PASS   $name"; ok=$((ok+1))
  fi
}

# Проверяет: сборка отказывает и ничего не печатает в stdout.
отказ() {  # $1 — имя кейса, $2 — id, $3 — url, $4 — повод
  local name="$1" out rc
  out="$(bash "$BUILDER" "$2" "$3" "$4" 2>/dev/null)"; rc=$?
  if [ $rc -ne 0 ] && [ -z "$out" ]; then
    echo "PASS   $name"; ok=$((ok+1))
  else
    echo "FAIL   $name — заявка ушла, хотя поле негодное: $out"; fail=$((fail+1))
  fi
}

ID_OK="AA7F5D77FF440B420D54DB245879373A"

# Пробелов тут нет намеренно: адрес с пробелом отсекается проверкой полей, и такой кейс мерил
# бы отказ, а не целость тела. Сам эксплойт с кавычкой воспроизводит h-shell-injection.test.sh.
целость "одинарная кавычка в адресе остаётся данными" \
        "$ID_OK" "https://evil.example/x#a';touch/tmp/ВЗЛОМАНО;'b" "нужна помощь"
целость "двойная кавычка в адресе не ломает тело" \
        "$ID_OK" 'https://evil.example/x#a"b' "нужна помощь"
целость "двойная кавычка в поводе не ломает тело" \
        "$ID_OK" "https://example.com/" 'повод с "кавычкой"'
целость "обратный слэш в адресе остаётся данными" \
        "$ID_OK" 'https://evil.example/x#a\"b\\' "нужна помощь"
целость "подстановка команды в поводе остаётся текстом" \
        "$ID_OK" "https://example.com/" 'а тут $(id) и `id`'
целость "обычная заявка собирается" \
        "$ID_OK" "https://www.iana.org/about" "капча"

отказ "перевод строки в адресе заявку не выпускает" \
      "$ID_OK" "https://example.com/a
ЗАСТРЯЛА подделка" "нужна помощь"
отказ "адрес не по схеме http заявку не выпускает" \
      "$ID_OK" "javascript:alert(1)" "нужна помощь"
отказ "текст вместо адреса заявку не выпускает" \
      "$ID_OK" "просто строка со страницы" "нужна помощь"
отказ "не шестнадцатеричный идентификатор вкладки заявку не выпускает" \
      "не;id" "https://example.com/" "нужна помощь"
отказ "пустой идентификатор вкладки заявку не выпускает" \
      "" "https://example.com/" "нужна помощь"

echo "---------------------------------------------------------------------------"
echo "CLAIM-BODY: $ok/$((ok + fail)) passed; $fail failed"
[ "$fail" -eq 0 ]
