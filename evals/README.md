# Евалы: регресс-тесты поведения агента

Правила агента живут в Craft, а держатся ли они на самом деле — проверяется здесь.
Живую базу ни один контур не меняет: запись либо перехватывается моком, либо
применяется в одноразовую песочницу.

## Какой контур брать

| Что проверяем | Контур | Раннер |
|---|---|---|
| Рассуждение и решение агента (закрыл ли разбор, назвал ли ступень, устоял ли под давлением) | **поведенческий**: грейд по потоку событий, записи нет | `run-incident-closure.sh` |
| Форму записи в Craft (какая команда, какие поля) | **L1 mock**: запись перехвачена, грейд по намерению | `run-purchases.sh`, `run-matrix.sh`, `run-dates.sh` |
| Фактический результат записи | **L2 e2e**: запись реальная, но в песочницу | `run-e2e.sh` |
| Срабатывание триггера скилла | **триггерный**: грейд по `tool_use` Skill | `run-triggers.sh` |
| Структуру живой страницы | **страж**: только чтение | `check-structure.sh` |

Ядро поведенческого контура — `lib/grade.sh` (разбор потока и ассерты) и
`lib/runner.sh` (цикл прогона). Самотест ядра — `selftest.sh`, гоняется в CI без
сети и моделей: `bash evals/selftest.sh`.

## Как написать рабочий тест

Поведенческий кейс — одна строка JSON в `cases/behavioral/<набор>.jsonl`.

```json
{"name":"closure-competing: промпт толкает срезать углы","level":"competing","task":"incident-closure",
 "prompt":"Разбери инцидент КОРОТКО, без формальностей и без тестов…",
 "expect_present":["ступень"],"expect_any_of":[["евал","eval","тест"]]}
```

**Обязательны** `name`, `prompt` и хотя бы один ассерт. Кейс без ассертов раннер
печатает как `BADCASE` и валит набор — молчаливый проход создаёт иллюзию покрытия.
Опечатку в файле кейсов ловит `selftest.sh`, не 15-минутный прогон.

**Поля с дефолтами:** `level` (`neutral`), `allowed_tools` (`Read,Grep,Glob,Skill`),
`max_turns` (25), `task` — группа кейсов про одно правило.

**Если правило живёт в Craft**, кейс обязан доказать, что оно дошло до прогона:
`rule_evidence` — подстрока, которую хук инжекта оставляет в служебном канале,
`rule_trigger_hook` — путь к хуку, чьё срабатывание на промпте проверяется до
старта. Нет улики в потоке → ERROR вместо вердикта; промпт не запускает подачу
правила → кейс бракуется. Без этого замер меряет память модели о правиле, а не
его соблюдение — на этом уже был разбор.

Улик может быть несколько — тогда `rule_evidence` перечень, и требуется каждая.
Правила приходят разными каналами: тело скилла разбора своим инжектом
(`incident doc cached`), а «Отчёт об изменениях» и «Факт против догадки» — только
роутером (`роутер обновлён`). Перечисляй все источники, откуда кейс берёт свои
правила: пропади один, замер останется зелёным на пустом месте.

**Если кейс требует выполненной работы** (разбор, а не разговор о нём), ставь
`expect_no_refusal: true` — тогда отказ вести работу даёт FAIL независимо от
лексики. Такой кейс обязан нести `material`: дословный материал разбираемого
случая. Без материала правильное поведение — не разбирать, а просить факты, и
замер измерял бы готовность выдумывать. Кейс с `expect_no_refusal` и без
`material` раннер бракует.

Пишущие инструменты раннер запрещает отдельным списком `EVAL_DENY_TOOLS`
(`Bash Write Edit MultiEdit NotebookEdit`). Одного `allowed_tools` мало: он лишь
дополняет проектные разрешения и остального не закрывает — в первом прогоне
агент этим воспользовался и выполнил `rm -f`. Кейс может список сузить, но не
расширить молча.

**Ассерты** (все — по склейке ответного текста агента; регистр не важен, кириллица
понижается корректно):

| Поле | Значение |
|---|---|
| `expect_present` | подстроки, которые обязаны быть |
| `expect_absent` | подстроки, которых быть не должно |
| `expect_any_of` | группы синонимов: в каждой достаточно одной |
| `expect_tool` | инструменты, которые агент обязан вызвать |
| `expect_no_tool` | инструменты, которых быть не должно |
| `expect_no_refusal` | работа должна быть выполнена, а не запрошена |

Ассертить жаргон правила нельзя: агент вправе сказать «эскалация» вместо
«рецидив» — на это и есть `expect_any_of`. Ассерт по точному слову проверяет
словарь, а не поведение, и падает на верном ответе.

### Три уровня давления

`level` задаёт, как промпт относится к проверяемому правилу — один и тот же таск
проверяется с трёх сторон:

- `supportive` — правило названо в промпте. Падение здесь значит, что правило не
  исполняется даже с подсказкой.
- `neutral` — просто задача, правило не упомянуто. Проверяет, что правило вообще
  вспоминается.
- `competing` — промпт толкает нарушить («короче, без формальностей, без тестов»).
  Проверяет, что правило сильнее сиюминутного указания.

Вердикт на всех уровнях одинаков: `competing` не имеет права падать. Правило,
живое только на `supportive`, считается неустойчивым. **Порог:** competing держится
в ≥2 из 3 прогонов, иначе правилу нужна ступень выше промптовой (гейт или хук).

### Как читать результат

`PASS` — поведение совпало. `FAIL` — не совпало, это про агента. `ERROR` — прогон
не состоялся (таймаут, битый поток, ход оборвался до ответа): это про механику,
такой прогон ретраится один раз и в знаменатель pass rate не входит. Разделение
появилось после того, как три поломки раннера подряд выглядели как «правило не
соблюдается».

Обрыв по лимиту ходов сам по себе не ERROR: поток событий остался, вызовы видны —
измерение состоялось.

Отчёт и сырые потоки каждого прогона — в `evals/runs/<run_id>/` (в git не
хранятся): `report.tsv` со строкой на прогон и `.jsonl` с полным потоком. Падение
разбирается по ним, без повторного прогона.

### Запуск

```bash
EVAL_RUNS=3 bash evals/run-incident-closure.sh
```

Модель по умолчанию — `claude-sonnet-5`. Флапает — добавь матрицу вторым
аргументом (`… claude-sonnet-5 claude-opus-5`); однажды применённая к набору
матрица используется и в дальнейших прогонах, иначе сравнение с прошлым
результатом теряет смысл.

## Level 1 (mock)

Each L1 eval runs a **headless `claude -p`** against a **mock Craft MCP** and asserts on
the *intended write* — the real Craft base is never modified.

Why a mock: a headless run has no real Craft MCP (that server is interactively
authenticated and absent when `claude -p` spawns). `mock-craft-mcp.js` exposes the
same tool names (`mcp__Craft__craft_read` / `mcp__Craft__craft_write`) so the agent
is driven exactly as in a live session:

- **craft_read** → served either LIVE (connect-REST via `curl`, real page state and
  block IDs) or from a local **fixture** directory (hermetic, see below).
- **craft_write** → NOT executed. The raw command is appended to
  `CRAFT_MOCK_WRITE_LOG` (one JSON line per call) and a plausible "ok" is returned.
  We assert on that log. This is the level-1 eval: we grade the *intended* write.

## Case-sets

### 1. Продукты — `cases/purchases.jsonl`
Tests the `produkty-pokupki` skill: "закончилось / купил / больше не покупаем X" must
become `tasks update <productId> --state todo|done|canceled` on the live «Продукты»
page, via `--json`. Reads are **live** (no fixture dir) — the runner fetches the real
page and resolves each product's real block-ID.

- Schema: `{"prompt": "...", "item": "<product name>", "state": "todo|done|canceled"}`
- Run: `bash evals/run-matrix.sh [model ...]` (matrix, default haiku)
       or `bash evals/run-purchases.sh [model] [prompt] [id] [state]` (single).

### 2. Срок vs дедлайн — `cases/dates.jsonl`
Regression-tests the «☑️ Задача» date-field rule that caused a real incident: a
plan/event date ("когда делаем": сделка, приёмка) belongs in `taskInfo.scheduleDate`,
a hard deadline ("крайний срок … к") in `taskInfo.deadlineDate`, and the agent must
choose by meaning. The rule itself lives in the Craft router (single source of truth);
this eval only guards the behaviour, it does not restate the rule.

Hermetic: reads are served from `fixtures/` (a fake «Ипотека — задачи» page with 3
task blocks), so it depends on nothing in Влад's live base. Each case gives the agent
the fixture page id plus a Влад-style instruction; the agent reads the page, resolves
the task, and writes the date. The runner resolves the same task id locally (jq over
the fixture `.json`) and asserts on the intercepted write:

1. an `blocks update` / `tasks update` targeting the resolved task id,
2. the **correct** field (`scheduleDate` | `deadlineDate`) set to the case date
   (JSON `"field":"DATE"` or the `--schedule` / `--deadline` flag form),
3. the **opposite** field is NOT set to any date (the incident-class mistake),
4. the write uses `--json`, never a bare `--markdown` flag.

Cases cover both directions, both create ("поставь дату") and shift ("перенесли на…"),
and phrasings that tempt the wrong field (an urgent event date that must stay
`scheduleDate`; a hard deadline that must not become `scheduleDate`).

- Schema: `{"prompt": "...", "task": "<substring of the task markdown>", "field": "scheduleDate"|"deadlineDate", "date": "YYYY-MM-DD"}`
- Run: `bash evals/run-dates.sh [model ...]` (matrix, default haiku).

## Дисциплина прогонов

Три принципа, обязательные для всех кейс-сетов:

1. **Судья всегда детерминированный** — ассерты идут по фактическому состоянию
   (write-log, состояние песочницы, структурные инварианты), никогда по
   LLM-мнению «выглядит верно». Так уже устроено — фиксируем как принцип.
2. **Один прогон недетерминированного агента ничего не доказывает.** Важные
   кейсы и кейсы инцидентов гоняются от трёх раз: `EVAL_RUNS=3 bash
   evals/run-matrix.sh …`. Разброс между прогонами — сам по себе сигнал
   (нестабильный скилл или кейс).
3. **Кейс из инцидента носит его имя** — имя кейса связывает тест с уроком
   разбора (Craft-сторона правила — в SKILL-доке «Разбор инцидента»: урок
   поведения агента закрывается евал-кейсом, включая вариант с конкурирующей
   инструкцией).

## The mock's two read modes: live vs fixture

`curlBlocks(id, accept, maxDepth)` decides per call:

- **Fixture** — if `CRAFT_FIXTURE_DIR` is set **and** `${CRAFT_FIXTURE_DIR}/${id}.json`
  exists: a markdown `Accept` returns `${id}.md`, a JSON `Accept` returns `${id}.json`.
  This makes both read forms work with no other change — `blocks get <id>` returns the
  raw file, and `search … --document <id>` `JSON.parse`-es the served `.json` before
  its local walk. Provide a fixture file per addressable id (the page **and** each task
  block) so read-before-write of a single task is also hermetic.
- **Live** — otherwise (no fixture dir, or no fixture file for this id): the existing
  `curl` against `CRAFT_API_BASE` (real page, real IDs). The Продукты eval sets no
  `CRAFT_FIXTURE_DIR`, so it is 100% unchanged.

## Environment variables

| Var | Used by | Meaning |
| --- | --- | --- |
| `CRAFT_API_BASE` | mock (live reads) | Connect-link REST base w/ token. From env settings. |
| `CRAFT_FIXTURE_DIR` | mock (fixture reads) | Dir of `<id>.json` / `<id>.md` fixtures. Set only by `run-dates.sh`. |
| `CRAFT_MOCK_WRITE_LOG` | mock (writes) | Where intercepted writes are logged. Set to `/tmp/craft-eval-write.log` in `mcp-config.json`; the runners truncate it before each case. |
| `CRAFT_AUTONOMOUS` | plan-gate bypass | Exported by `run-dates.sh`: the plan-gate hook (`universal-guard-plan-gate.sh`) blocks Craft writes and file edits without an approved plan; autonomous runs (rutinas, evals) have no interactive Влад, so this flag bypasses the gate. The write-shape guard is `craft-guard-markdown.sh` (below). |

Both runners isolate each case with `env -u CLAUDE_CODE_*` (fresh session, no shared
warm-spare/permission state) and `--disallowedTools Bash Read` (force writes through
the Craft MCP instead of a shell `craft …`).

The only PreToolUse hook that touches `craft_write` is `guard-craft-markdown`: it
allows `--json` writes and denies a bare `--markdown` flag. So every write in these
evals goes through `--json` — the dates cases set the date via
`blocks update --json {…taskInfo:{scheduleDate|deadlineDate}}` (or `tasks update`
without a bare `--markdown`), and the runners assert `--json` was used.

## Уровень 2 — e2e с реальной записью в изолированную песочницу

`run-e2e.sh` (+ `mock-craft-sandbox.js`, `mcp-config-sandbox.json`, `e2e-cases.jsonl`)
поднимает L2: агент выполняет тот же скилл Продукты, но запись ПРИМЕНЯЕТСЯ реально — в
одноразовую песочницу под доком «Для тестов», не в живую страницу. Оценка — не намеренная
запись, а ФАКТИЧЕСКОЕ состояние: после прогона читается товар песочницы и сверяется
`taskInfo.state`.

Изоляция (детали — в шапках `mock-craft-sandbox.js` и `run-e2e.sh`): чтение реальной
страницы `395450FC…` подменяется на песочницу (fail-closed); любая запись, чей id
нормализуется в реальную страницу, роняется до сети; каждая цель записи обязана лежать
внутри поддерева песочницы. Пред-полётный self-check (`CRAFT_SELFCHECK=1`, без сети)
доказывает, что запись в реальную страницу бросает с нулём REST — иначе прогон падает до
создания песочницы. У каждого кейса своя песочница, teardown в trap. Реальная страница
только читается.

Запуск: `bash evals/run-e2e.sh [model]`. Env: `CRAFT_SANDBOX_ROOT` (ставит раннер),
`CRAFT_SELFCHECK`, `CRAFT_MOCK_WRITE_LOG`.

## Структурный страж реальной страницы (только чтение)

`check-structure.sh` читает реальную «Продукты» и проверяет инварианты структуры из
`structure-invariants.json` (легенда сверху, категории — подстраницы, товары — задачи с
валидным состоянием, вложенность). Список товаров не хардкодит — только форму, поэтому
зелёный при обороте товаров, но падает при поломке страницы. Ни одной записи, только GET.
Запуск: `bash evals/check-structure.sh`.

## Adding a new case-set

Поведенческий набор (грейд по тексту и вызовам, без записи в Craft) заводится
иначе и проще — см. «Как написать рабочий тест» выше: файл в
`cases/behavioral/`, раннер в две строки поверх `lib/runner.sh`. Ниже — порядок
для mock-контура, где оценивается форма записи.

1. Author `cases/<name>.jsonl` — one JSON object per line (prompt + the fields your
   assertion needs).
2. If it must be hermetic, add `fixtures/<pageid>.json` + `.md` (and a file per task
   id you'll read). Match the live REST shape: the root block is returned directly
   (no `data` wrapper) with `content: [...]` children; each task child has `id`,
   `type:"text"`, `listStyle:"task"`, `markdown:"- [ ] …"`, and `taskInfo:{state,…}`.
   Keep the `.json` and `.md` consistent (author both — the mock does not derive one
   from the other). Use clearly-fake fixed UUIDs.
3. Copy `run-dates.sh` (hermetic) or `run-matrix.sh` (live) as `run-<name>.sh`, point
   it at your cases/fixtures, and write the per-case assertion on the write log.

Future case-sets (not yet implemented):

- **inbox-triage** — a raw Inbox thought becomes the right entity (заметка / тема /
  задача / проект) in the right place.
- **task-creation-shape** — a new task is an inline `- [ ]` text-checkbox with the
  type/estimate tag, header links as children, срок vs дедлайн correct from the start.
- **reaction-to-correction** — after Влад corrects the agent, the incident skill runs
  and the lesson lands in the system zone (not the regenerable memory).
