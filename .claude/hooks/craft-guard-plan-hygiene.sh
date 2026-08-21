#!/usr/bin/env bash
# PreToolUse guard on plan writes (Write/Edit/MultiEdit to */plans/*.md). Rules 3, 5 and 6
# run first and apply to EVERY plan; rules 0–2 and 4 police ONLY Craft «План правок»
# files — detected by their structure (a «где:» locator line, a Craft link/ref or a
# сущностный заголовок) — and pass plans про КОД through untouched, since those
# legitimately name files, commands and IDs.
#
# Enforces seven of Влад's plan rules that the built-in Plan-mode actively pushes
# against (it templates a verification/order section and doesn't produce links):
#   0. NO fence longer than three backticks — a nested code block reads worse
#      than a quote, for humans and for gates alike.
#   1. NO execution mechanics in a plan — no verification/order sections, no
#      commands (blocks get/update, tasks, git, curl, --json/--id). A plan says
#      WHAT changes and WHERE, not HOW to do or verify it.
#   2. Block references are clickable docs.craft.do links, not bare UUIDs.
#   3. NO hard-wrapped paragraphs — Влад reads plans on a phone, where every
#      wrapped line renders as its own paragraph and the sentence breaks apart.
#   4. EVERY entity block carries its «где:» locator — a plan without an address
#      cannot be executed, and the critic used to spend a whole round on it.
#   5. NO relative file paths — inside a plan file the app resolves a path
#      against the PLANS folder, not the session root, so a relative path opens
#      nothing. Applies to markdown links and to bare mentions alike: the app
#      makes a path in plain text clickable too.
#   6. NO link label repeating its own absolute address — the address stays
#      absolute, the label is trimmed to a recognisable root, or the line turns
#      into a wall of slashes on a phone screen.
#
# Heuristic — narrow patterns to limit false positives; on a hit it denies the
# write with a reason so the plan gets rewritten. Fail quiet on anything odd.
set -u
# POSIX locale skips case-folding for Cyrillic in grep -i; force UTF-8 so the
# Cyrillic section headings (Порядок/Проверка/…) match case-insensitively.
export LC_ALL=C.UTF-8

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
case "$tool" in Write|Edit|MultiEdit) ;; *) exit 0 ;; esac

fp="$(jq -r '.tool_input.file_path // ""' <<<"$input" 2>/dev/null)"
[[ "$fp" == */plans/*.md ]] || exit 0

# Записываемый текст лежит в разных полях у разных инструментов: целиком в content,
# одиночным фрагментом в new_string и ПАЧКОЙ фрагментов в edits[].new_string. Читать
# только первые два — значит пропускать пакетную правку насквозь: у неё верхние поля
# пусты, проверка выходила на пустом содержимом и правила не работали вовсе.
#
# Фрагменты склеиваются ЧЕРЕЗ ПУСТУЮ СТРОКУ: встык хвост одного и начало другого
# прочитались бы соседями и дали ложный жёсткий перенос, которого в файле нет.
# Известная граница: незакрытый забор кода внутри фрагмента гасит проверки для
# остатка пачки — это промах, а не ложный отказ.
content="$(jq -r '[(.tool_input.content // empty),
                   (.tool_input.new_string // empty),
                   ((.tool_input.edits // [])[] | .new_string // empty)]
                  | join("\n\n")' <<<"$input" 2>/dev/null)"
[[ -n "${content//[[:space:]]/}" ]] || exit 0

# Обе чистки ниже снимают с текста код-заборы, поэтому опознание забора живёт одной
# функцией. Забор — ряд бэктиков ИЛИ тильд от трёх знаков с любым отступом; у
# открывающего запоминаются знак и длина, и закрыть блок может лишь ряд того же знака
# не короче. Без длины забор из четырёх бэктиков рвётся вложенным примером из трёх, и
# остаток блока уезжает в проверки как проза; порог в три знака отделяет забор от
# зачёркивания ~~текст~~. Длину ряда считаем посимвольно: интервалам {3,} в mawk верить
# нельзя — он отдаёт на них длину 3 и правило длины молча выключается.
#   blank — гасит строки блока ПУСТЫМИ (плюс цитаты, таблицы и заголовки): удаление
#           придвинуло бы друг к другу несоседние строки и родило ложный перенос.
#   drop  — выбрасывает строки блока совсем, оставляя только прозу плана.
strip_fenced() {
  awk -v mode="$1" '
    BEGIN{f=0; fc=""; fl=0}
    {
      t=$0; sub(/^[[:space:]]*/,"",t)
      ch=substr(t,1,1); n=0
      if (ch=="`" || ch=="~") { while (substr(t,n+1,1)==ch) n++ }
      fence=0
      if (n>=3) {
        if (f==0)                 { f=1; fc=ch; fl=n; fence=1 }
        else if (ch==fc && n>=fl) { f=0; fence=1 }
      }
      if (fence || f) { if (mode=="blank") print ""; next }
      if (mode=="blank" && ($0 ~ /^[[:space:]]*>/ || $0 ~ /^[[:space:]]*\|/ || $0 ~ /^[[:space:]]*#/)) {
        print ""; next
      }
      print
    }'
}

# 3. Жёсткий перенос абзаца. Правило действует на ЛЮБОЙ план, поэтому проверка стоит
# ДО детекта Craft-плана: Влад читает планы с телефона, где каждая перенесённая строка
# рисуется отдельным абзацем и фраза рвётся посреди себя.
#
# Счёт длины требует UTF-8: в POSIX-локали кириллица весит вдвое, порог упал бы до ~31
# символа и отбивал бы законные планы. Локаль проверяется пробой, а не именем — на маке
# набор локалей другой. Нет ни одной подходящей → проверка молча выключается, остальные
# продолжают работать (файл держится «fail quiet on anything odd»).
wrap_probe="абвгд"
if (( ${#wrap_probe} != 5 )); then
  for wrap_loc in C.UTF-8 C.utf8 en_US.UTF-8 en_US.utf8 ru_RU.UTF-8 ru_RU.utf8; do
    LC_ALL="$wrap_loc"
    (( ${#wrap_probe} == 5 )) && break
  done
fi
if (( ${#wrap_probe} == 5 )); then
  # Строки, где перенос — часть содержимого (цитата, код, таблица, заголовок), гасятся
  # ПУСТЫМИ, а не удаляются: удаление придвинуло бы друг к другу несоседние строки и
  # родило ложное срабатывание.
  wrapbody="$(strip_fenced blank <<<"$content")"

  wrap_hit=0
  wrap_prev=""
  while IFS= read -r wrap_line || [[ -n "$wrap_line" ]]; do
    if [[ -n "${wrap_prev//[[:space:]]/}" ]]; then
      # Длина — по тексту строки: без ведущих пробелов и без маркера списка. Отступ на
      # продолжении — самый частый стиль переноса, и он не должен прятать дефект.
      p="${wrap_prev#"${wrap_prev%%[![:space:]]*}"}"
      # Маркер нумерованного пункта — любой длины и в обеих формах, «10.» и «10)»:
      # одноцифровой шаблон принимал десятый пункт за продолжение девятого.
      p="${p#[-*+] }"
      [[ "$p" =~ ^[0-9]+[.\)][[:space:]] ]] && p="${p#"${BASH_REMATCH[0]}"}"
      c="${wrap_line#"${wrap_line%%[![:space:]]*}"}"
      c_isitem=0
      [[ "$c" == [-*+]\ * ]] && c_isitem=1
      [[ "$c" =~ ^[0-9]+[.\)][[:space:]] ]] && c_isitem=1
      # Локатор «где:» и строка со ссылкой исключены: их длину задаёт адрес, а не вёрстка.
      if (( ${#p} >= 60 )) && [[ "$p" != где:* && "$wrap_prev" != *http* && -n "$c" ]] \
         && (( ! c_isitem )) && [[ "$c" != '---'* && "$c" != '!['* ]]; then
        wrap_hit=1
        break
      fi
    fi
    wrap_prev="$wrap_line"
  done <<<"$wrapbody"

  if (( wrap_hit )); then
    # Свой отказ, не общий: общая причина требует Craft-ссылок и на код-плане соврала бы,
    # за что отбили.
    wrap_reason="План свёрстан жёсткими переносами: абзац разбит на строки под ширину терминала. Влад читает с телефона, где каждая такая строка рисуется отдельным абзацем и фраза рвётся посреди себя. Набери абзац одной строкой; перенос оставь только внутри цитат, кода и таблиц — в цитате знак «>» ставится на КАЖДОЙ строке, иначе продолжение читается как проза."
    jq -cn --arg r "$wrap_reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
    exit 0
  fi
fi

# 5. Относительный путь к файлу. Правило действует на ЛЮБОЙ план — улика пришла как раз
# из плана про код, — поэтому проверка тоже стоит ДО детекта Craft-плана. Внутри файла
# плана база относительного пути — папка планов, а не корень сессии, так что ссылка ведёт
# в никуда; кликается и голое упоминание пути текстом, поэтому проверяются оба вида.
#
# Дефектом считается только путь, который РЕЗОЛВИТСЯ в живой ФАЙЛ от корня сессии: значит
# автор имел в виду реальный файл и записал его относительно. Неразрешимый путь и имя
# файла без косой черты не трогаются — это прозаическое упоминание, а не адрес. Каталог —
# тоже не адрес: хлебная крошка «где: репозиторий › каталог › …» называет место, а не файл,
# и по -e вместо -f она ловилась как дефект.
# Цитаты и заборы кода снимаются: там путь — содержимое, а не адрес.
plan_root="$(jq -r '.cwd // ""' <<<"$input" 2>/dev/null)" || plan_root=""
[[ -n "$plan_root" ]] || plan_root="${CLAUDE_PROJECT_DIR:-$PWD}"
pathbody="$(strip_fenced drop <<<"$content" | grep -v '^[[:space:]]*>')"
# Адрес ссылки и голый текст разбираются порознь. Текст ссылки в квадратных скобках —
# подпись, а не адрес: у `[tests/run.sh](/абсолютный/путь)` ссылка исправна, и ловить в
# ней ОТНОСИТЕЛЬНОСТЬ нельзя — так и задумано. Проверяется у подписи другое: не
# повторяет ли она свой абсолютный адрес целиком (правило ниже) — тогда строка
# превращается в полотно из слешей. Сперва снимаются адреса, потом ссылки удаляются
# целиком, и только остаток читается как проза.
plan_cands="$(grep -oE '\]\([^)]*\)' <<<"$pathbody" | sed -E 's/^\]\(//; s/\)$//; s/^<//; s/>$//')"
plan_rest="$(sed -E 's/\[[^]]*\]\([^)]*\)//g' <<<"$pathbody")"
# Из остатка убираются адреса, у которых относительной части быть не может: URL со схемой
# и абсолютные пути. Иначе хвост URL и середина абсолютного пути читаются как относительный.
plan_rest="$(sed -E 's#[a-zA-Z][a-zA-Z0-9+.-]*://[^ )]*##g; s#(^|[[:space:]([])[~/][^ )]*#\1#g' <<<"$plan_rest")"
plan_cands+=$'\n'"$(grep -oE '(^|[^A-Za-z0-9_./-])[A-Za-z0-9_@.-]+(/[A-Za-z0-9_@.+-]+)+' <<<"$plan_rest" \
                    | sed -E 's/^[^A-Za-z0-9_.-]+//')"

rel_hits=()
while IFS= read -r cand; do
  [[ -z "${cand//[[:space:]]/}" ]] && continue
  cand="${cand%%#*}"
  cand="$(sed -E 's/:[0-9]+(-[0-9]+)?$//' <<<"$cand")"
  [[ -z "$cand" || "$cand" == /* || "$cand" == \~* ]] && continue
  [[ "$cand" =~ ^[a-zA-Z][a-zA-Z0-9+.-]*:// ]] && continue
  [[ -f "$plan_root/$cand" ]] || continue
  rel_hits+=("$cand")
done < <(sort -u <<<"$plan_cands")

if [[ ${#rel_hits[@]} -gt 0 ]]; then
  # Свой отказ, не общий: общая причина требует Craft-ссылок и на код-плане соврала бы.
  rel_list="$(printf '%s, ' "${rel_hits[@]}")"
  path_reason="В плане относительные пути к файлам: ${rel_list%, } — у Влада они не откроются. Внутри файла плана путь резолвится от папки планов, а не от корня сессии, и кликается даже голое упоминание в тексте. Дай абсолютный путь от корня диска."
  jq -cn --arg r "$path_reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
fi

# 6. Подпись, дословно повторяющая свой абсолютный адрес. Признак без порогов на длину:
# адресу положено быть абсолютным, подписи — обрезанной до узнаваемого корня, поэтому
# их равенство и есть неукороченная подпись.
dup_hits=()
while IFS= read -r pair; do
  [[ -z "${pair//[[:space:]]/}" ]] && continue
  dl="${pair%%\](*}"; dl="${dl#[}"
  da="${pair#*\](}"; da="${da%)}"
  [[ "$da" == /* || "$da" == \~/* ]] || continue
  [[ "$dl" == "$da" ]] || continue
  dup_hits+=("$dl")
done < <(grep -oE '\[[^]]*\]\([^)]*\)' <<<"$pathbody" | sort -u)

if [[ ${#dup_hits[@]} -gt 0 ]]; then
  dup_list="$(printf '%s, ' "${dup_hits[@]}")"
  dup_reason="В плане подписью ссылки показан абсолютный путь целиком: ${dup_list%, } — Влад читает план с телефона, и строка превращается в полотно из слешей. Адрес оставь абсолютным, подпись обрежь до узнаваемого корня — репозитория, маунта или пакета."
  jq -cn --arg r "$dup_reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
fi

# Only Craft-plans («План правок») are policed by rules 0–2 and 4. A plan про КОД
# legitimately names files, commands and flags, so the mechanics/command/ID checks below
# must not touch it. Detect a Craft-plan by its structural signals — the «где:» locator
# line every entity carries, a Craft link/ref, or a сущностный заголовок вида
# «## [тип · операция]» — and pass anything else (a code or other plan) straight through.
#
# Заголовок сущности в списке сигналов обязателен: без него план, где адрес забыт у ВСЕХ
# сущностей разом, не опознавался Craft-планом и проезжал мимо правила 4 — то есть мимо
# ровно того случая, ради которого правило заведено.
grep -qE 'docs\.craft\.do|block://|(^|[[:space:]])где:|^[[:space:]]*#+[[:space:]]*\[' <<<"$content" || exit 0

# Dictated verbatim text in a «План правок» sits in a QUOTE block, and code examples in
# ``` or ~~~ fences; both may legitimately contain command tokens, IDs, even a «Проверка»
# heading — that is the content being written, not plan mechanics. Strip fences and
# quote lines first so only the plan's own PROSE is policed.
body="$(strip_fenced drop <<<"$content")"
# Строки цитаты — дословный записываемый текст, а не проза плана: в нём законны и
# команды, и голые идентификаторы, поэтому из проверки они уходят.
body="$(grep -v '^[[:space:]]*>' <<<"$body")"

problems=()

# 0. Забор длиннее трёх бэктиков — вложенный блок кода: людьми и гейтами он читается
# хуже цитаты. Только на записи ЦЕЛОГО файла: на правке фрагмента длинный забор бывает
# законной половиной пары, целого текста хук не видит и судить не может. Инварианта это
# не даёт (шелл-запись гейтом не покрыта) — правило гигиены, не опора для других гейтов.
if [[ -n "$(jq -r '.tool_input.content // ""' <<<"$input" 2>/dev/null)" ]] \
   && grep -qE '^[[:space:]]*````' <<<"$content"; then
  problems+=("забор длиннее трёх бэктиков — вложенный блок кода в плане запрещён, показывай вложенный пример цитатой")
fi

# 1a. verification / order / test section headings
if grep -qiE '^#+[[:space:]]*(Порядок|Проверка|Verification|Verify|Тесты|Testing|Проверка результата|Порядок выполнения)' <<<"$body"; then
  problems+=("секция механики/проверки/порядка — механика в план не выносится")
fi
# 1b. explicit execution commands / mechanics tokens
if grep -qE '(blocks (get|update|add|move|delete|learn)|tasks (update|add|delete)|(^|[[:space:]])--(json|id|markdown|siblingId|depth)([[:space:]]|=)|git (commit|push|add)|curl )' <<<"$body"; then
  problems+=("команды/механика выполнения в тексте плана")
fi

# 4. Сущность без адреса правки. Сущностный блок — заголовок ЛЮБОГО уровня, открытый
# скобкой типа и операции («## [заметка · новая] …»), и строки до следующего заголовка;
# такой блок обязан нести строку «где:». Строки юнита вне сущностного блока не смотрим:
# у мета юнита свои лейблы («приёмка:», «риск:»), словаря операций они не нарушают.
# Только на записи ЦЕЛОГО файла: на правке фрагмента хук целого текста не видит, и
# сущность без адреса там законна — адрес остался в неправленой части.
if [[ -n "$(jq -r '.tool_input.content // ""' <<<"$input" 2>/dev/null)" ]] \
   && awk '
     /^[[:space:]]*#+[[:space:]]*\[/ { if (ent && !found) { bad=1; exit } ent=1; found=0; next }
     /^[[:space:]]*#/               { if (ent && !found) { bad=1; exit } ent=0; next }
     { if (ent && $0 ~ /^[[:space:]]*[-*+]?[[:space:]]*где:/) found=1 }
     END { if (!bad && ent && !found) bad=1; exit !bad }
   ' <<<"$body"; then
  problems+=("сущность без строки «где:» — у каждой сущности плана есть адрес правки, путь крошками от контейнера до позиции")
fi

# 2. bare block-IDs (UUID) not inside a docs.craft.do link.
# Strip all docs.craft.do URLs first; a UUID left in the remainder is bare.
stripped="$(sed -E 's#https?://docs\.craft\.do[^ )]*##g' <<<"$body")"
if grep -qiE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' <<<"$stripped"; then
  problems+=("голый block-ID вне ссылки — отсылка к блоку должна быть кликабельной ссылкой docs.craft.do")
fi

[[ ${#problems[@]} -eq 0 ]] && exit 0

reason="План нарушает правила: $(printf '%s; ' "${problems[@]}")План отвечает ЧТО меняется и КУДА ложится — механика, команды, проверка и порядок в него не выносятся; отсылки к блокам даются кликабельными ссылками docs.craft.do, не голым ID. Перепиши план и запиши снова."
jq -cn --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
