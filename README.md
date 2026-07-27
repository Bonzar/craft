# Система агента: Craft + код + любые проекты

Репозиторий — **механический слой** системы агента Влада: хуки, инжекторы,
инструменты (`craft-sync`), установка, тесты и эвалы. Знания и правила здесь не
живут — их канон в Craft.

## Карта системы (три слоя)

| Слой | Канон | Что содержит | Как попадает в сессию |
|---|---|---|---|
| **Craft** | база Craft | правила поведения и записи (роутер), правила кода, память о жизни, инстинкты | живой инжект SessionStart-хуками по connect-API; в чатах без репо — чтение роутера через MCP |
| **Репо (этот)** | git, PR-цикл | хуки `universal-*` (план-гейт, детект инцидентов) и `craft-*` (инжекты, гварды записи), craft-sync, install.sh, тесты, эвалы | в craft-сессиях — project-level из `.claude/settings.json`; на локальной машине — `install.sh` симлинкает универсальный слой в `~/.claude` (действует во всех проектах) |
| **Яндекс-слой** | локально `~/.claude` | arc/трекер-скиллы, arc-гварды | только рабочий мак; в GH не уезжает |

Слои хуков различаются префиксом файла: `universal-*` — работают в любой сессии
(устанавливаются в `~/.claude`), `craft-*` — только для этого репо.
Кэши контекста (`.claude/craft-*-context.md`, exempt-scope) — пер-сессионные
снэпшоты живого Craft, в git не попадают.

## План-гейт (главный предохранитель)

`universal-guard-plan-gate.sh` блокирует без одобренного в текущем ходе плана
(план-мод → ExitPlanMode):

- запись в Craft (`craft_write`), кроме операций целиком внутри предодобренных
  зон (`.claude/hooks/gate-exempt-pages.txt`, напр. «Продукты»);
- правку файлов где угодно (Write/Edit/MultiEdit/NotebookEdit) — охват
  инвертированный: гейтится всё, кроме план-файлов, tmp/scratchpad и служебных
  зон `~/.claude`.

Автономные прогоны (рутины, эвалы) обходят гейт через `CRAFT_AUTONOMOUS=1`.
Bash-запись (`sed -i`, `echo >`) не гейтится — осознанное ограничение.

## Установка

**Облако (craft-сессии):** ничего не нужно — тулчейн в образе, секреты в
настройках окружения, хуки project-level.

**Локально (macOS), универсальный слой во все проекты:**

```bash
bash ~/craft-local/install.sh
```

Симлинкает `universal-*` хуки в `~/.claude/hooks`, дописывает их регистрацию в
`~/.claude/settings.json` (идемпотентно, с бэкапом), создаёт
`~/.claude/craft.env` (600) с connect-доступом для сессий вне этого репо.
После установки перезапустить активные сессии.

**Новая облачная репа:** одна строка в bootstrap окружения —
`git clone <this-repo> ~/agent-system && bash ~/agent-system/install.sh`.

## Локальный сетап с нуля (macOS)

```bash
brew install go coreutils jq bash    # go >=1.24.7; gtimeout; bash >=4
ln -sf "$(brew --prefix)/bin/gtimeout" ~/.local/bin/timeout
```

- **node ≥ 22** — для mock-эвалов (`nvm install 22`); путь прописывается в
  eval-конфиги (ниже).
- **`claude` CLI** для headless-эвалов — статический бинарник без апдейтера:

  ```bash
  ver=$(curl -fsSL https://downloads.claude.ai/claude-code-releases/latest)
  plat="darwin-$(uname -m | sed 's/x86_64/x64/; s/aarch64/arm64/')"
  sum=$(curl -fsSL "https://downloads.claude.ai/claude-code-releases/$ver/manifest.json" | jq -r ".platforms[\"$plat\"].checksum")
  curl -fsSL -o ~/.local/bin/claude "https://downloads.claude.ai/claude-code-releases/$ver/$plat/claude"
  [ "$(shasum -a 256 ~/.local/bin/claude | cut -d' ' -f1)" = "$sum" ] && chmod +x ~/.local/bin/claude || echo "CHECKSUM MISMATCH"
  ```

- **PATH**: `~/.local/bin` (там `craft-sync`, `claude`, `timeout`).

### Секреты — `.env` в корне репо (не коммитится)

```dotenv
export CRAFT_API_BASE=https://connect.craft.do/links/XXXX/api/v1   # СЕКРЕТ (токен в URL)
export CRAFT_LINKS_STORE=<block-id>
export CRAFT_SYNC_BUILD=1
export DISABLE_AUTOUPDATER=1
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
```

Перед ручными прогонами: `source ./.env`. Вне репо хуки берут те же значения из
`~/.claude/craft.env` (создаёт install.sh).

### Локальные пути в eval-конфигах (вне git)

`evals/mcp-config.json` и `evals/mcp-config-sandbox.json` содержат абсолютные
облачные пути — локально перепиши `command`/`args` на свой node и путь репо и
сними с коммита: `git update-index --skip-worktree evals/mcp-config*.json`.

## Проверка готовности

```bash
craft-sync --help                 # бинарник собран хуком и на PATH
bash tests/run.sh                 # тесты хуков — exit 0
source ./.env
bash evals/check-structure.sh     # read-only структурный страж «Продукты»
bash evals/run-matrix.sh          # L1 mock: Продукты (по умолч. haiku)
bash evals/run-dates.sh           # L1 mock: срок vs дедлайн (герметичный)
bash evals/run-e2e.sh             # L2 e2e — только когда L1 зелёные
```

> Дефолтная модель эвалов — haiku, слабая на нюансных правилах: зелёный
> результат — признак способной модели, а не только корректного окружения.

## Известные ограничения

- **Пин MCP-ID в permissions**: `~/.claude/settings.json` может содержать
  allow-запись вида `mcp__<uuid>__craft_read` — при пере-подключении Craft MCP
  UUID меняется и запись умирает (permissions не принимают wildcard в имени
  сервера). Симптом — вернувшиеся permission-промпты на чтение Craft; лечение —
  обновить UUID в allow.
- **MCP-гигиена**: держать активными < 10 MCP-серверов на сессию — большой
  набор инструментов деградирует контекст задолго до лимита окна.

Документация компонентов: [CLAUDE.md](CLAUDE.md),
[craft-sync/README.md](craft-sync/README.md), [evals/README.md](evals/README.md),
[tests/README.md](tests/README.md).
