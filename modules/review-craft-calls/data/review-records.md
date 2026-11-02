## Записи поставщика Craft

- `write` — вызов записи в Craft: `tool`, `command`, `result` — ответ вызова, `types` — теги типа сущности из команды, `turn` — номер хода.
- `read` — вызов чтения: `command`, `ids` — id прочитанных блоков.
- `skill` — вызов скилла: `skill` — его slug.

Чтобы прочитать записанный блок, найди его id в `result` или `command` и вызови `mcp__‹сервер›__craft_read` с командой `blocks get ‹id› --depth -1`.
