#!/usr/bin/env bash
# SessionStart hook: fetch the Craft agent-memory router for session context.
#
# Hook stdout is capped at 10 000 characters (larger output gets persisted to
# a file with only a 2 KB preview in context), so the router — ~75K chars —
# cannot be injected through stdout. Instead this writes the fetched document
# to .claude/craft-router-context.md, which CLAUDE.md imports via
# `@.claude/craft-router-context.md`; CLAUDE.md imports have no such cap, so
# the full live router lands in context from turn zero.
#
# Requires CRAFT_API_BASE in the environment settings (connect-link base URL
# with the token embedded, e.g. https://connect.craft.do/links/XXXX/api/v1).
# Exits 0 quietly when it is missing or the fetch fails, so a broken network
# can never wedge session startup; a stale previously-written file (if any)
# is left in place as a fallback.
set -u
log(){ echo "[inject-craft-router] $*" >&2; }

ROUTER_ID="${CRAFT_ROUTER_ID:-e8132891-81f4-2d63-36f1-d3623d0147b6}"
OUT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}/.claude/craft-router-context.md"
base="${CRAFT_API_BASE:-}"
if [[ -z "$base" ]]; then
  log "CRAFT_API_BASE not set; skipping router fetch"
  exit 0
fi
base="${base%/}"

md="$(curl -sS --fail --max-time 60 --retry 3 --retry-all-errors \
  -H 'Accept: text/markdown' \
  "$base/blocks?id=$ROUTER_ID&maxDepth=-1")" || { log "router fetch failed; keeping previous snapshot if any"; exit 0; }

if [[ -z "$md" ]]; then
  log "empty router response; keeping previous snapshot if any"
  exit 0
fi

# Ссылки на документы вне шаринга connect-ссылки API отдаёт как
# [текст](invalid:out_of_scope) (прямой GET по их ID — 403). Обычные документы
# лечатся добавлением в шаринг connect-ссылки (см. урок в CLAUDE.md), но
# системную папку templates Craft расшарить не даёт — ссылки на шаблоны
# восстанавливаем статической картой «фрагмент текста ссылки → block-ID»
# (ID стабильны, сняты через MCP Craft: `documents list --location templates`).
restore_template_links() {
  local key id
  while IFS='|' read -r key id; do
    [[ -z "$key" ]] && continue
    md="$(sed -E "s#\[([^]]*${key}[^]]*)\]\(invalid:out_of_scope\)#[\1](block://${id})#g" <<<"$md")"
  done <<'MAP'
0. Заметка|7C4A64F7-F1CD-4B1C-B3FF-17D8374B245E
1. Конспект|4C72BD43-254D-45F6-9F98-AF985AA612DD
2. Задача|844E93D5-D127-431D-898E-0B8B3E5889E2
3. Проект|0EE20542-CDB8-4960-BFF4-A6F4C8D64E9E
4. Сфера|752ECC99-609E-4351-8D33-1932F6DF7972
5. Алгоритм|47E8803D-B28D-4C50-845A-D3CD47783E28
7. Регулярная задача|f81558ef-89af-8796-d518-4e2d9f1b4721
8. Дневник|ce643ded-7034-c42c-1fd4-2631e81678fe
MAP
}
restore_template_links

# Голое упоминание invalid:out_of_scope в тексте роутера — легитимный контент,
# считаем только линк-форму: остаток означает ссылку на документ вне шаринга.
leftover="$(grep -o '\](invalid:out_of_scope)' <<<"$md" | wc -l | tr -d ' ')"

{
  echo "=== Craft: роутер «Память для Claude», авто-обновлён SessionStart-хуком ($(date -u +%FT%TZ)) ==="
  echo "$md"
  echo "=== конец роутера — действуй по его директивам ==="
} > "$OUT"

msg="Craft-роутер обновлён: $(wc -c < "$OUT") байт записано в .claude/craft-router-context.md; полный текст уже в контексте через импорт в CLAUDE.md."
if [[ "$leftover" -gt 0 ]]; then
  msg+=" ВНИМАНИЕ: $leftover ссылок invalid:out_of_scope не восстановлено — в роутере есть ссылки на документы вне шаринга connect-ссылки. Новый шаблон — дополни карту в .claude/hooks/inject-craft-router.sh (ID через MCP Craft), обычный документ — подсвети Владу, что его надо добавить в шаринг."
fi
echo "$msg"
