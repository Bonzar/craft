# craft — агентское окружение

Память и правила заметочной базы Craft для Claude Code + инструменты (`craft-sync`),
хуки, тесты хуков и эвалы скиллов. Обзор — в [CLAUDE.md](CLAUDE.md); документация по
компонентам — [craft-sync/README.md](craft-sync/README.md), [evals/README.md](evals/README.md),
[tests/README.md](tests/README.md).

В облаке (Claude Code on the web) окружение поднимается само: тулчейн запечён в образ,
секреты приходят из настроек окружения, а сборка `craft-sync` и инжект контекста делают
`SessionStart`-хуки из [.claude/settings.json](.claude/settings.json). Ниже — что нужно
доставить **локально на macOS**, чтобы получить то же самое. Всё пер-сессионное (сборка
бинарника, инжект router/incident) после этого поднимают те же хуки — руками повторять не надо.

## Требования (macOS)

Через Homebrew — ставится только недостающее:

```bash
brew install go coreutils jq bash    # go >=1.24.7; coreutils даёт gtimeout; bash >=4 (штатный 3.2 не тянет тесты/раннеры)
ln -sf "$(brew --prefix)/bin/gtimeout" ~/.local/bin/timeout   # раннеры зовут `timeout` без префикса
```

- **node ≥ 22** — эвалы-mock запускаются на нём. Через nvm: `nvm install 22`.
  Абсолютный путь к этому `node` прописывается в eval-конфиги (см. ниже).
- **`claude` CLI** — для headless-эвалов (`claude -p`). Ставить **статическим бинарником
  без лаунчера/авто-апдейтера** (чтобы бинарник не порождал фоновых процессов):

  ```bash
  # скачивает и кладёт в ~/.local/bin/claude, минуя `claude install` (тот ставит фоновый апдейтер)
  ver=$(curl -fsSL https://downloads.claude.ai/claude-code-releases/latest)
  plat="darwin-$(uname -m | sed 's/x86_64/x64/; s/aarch64/arm64/')"
  sum=$(curl -fsSL "https://downloads.claude.ai/claude-code-releases/$ver/manifest.json" | jq -r ".platforms[\"$plat\"].checksum")
  curl -fsSL -o ~/.local/bin/claude "https://downloads.claude.ai/claude-code-releases/$ver/$plat/claude"
  [ "$(shasum -a 256 ~/.local/bin/claude | cut -d' ' -f1)" = "$sum" ] && chmod +x ~/.local/bin/claude || echo "CHECKSUM MISMATCH"
  ```

- **PATH** — `~/.local/bin` должен быть в PATH (там `craft-sync`, `claude`, `timeout`).

## Секреты и env — `.env` (не коммитится)

Облако инжектит их из настроек окружения; локально — в `.env` в корне репы (он в `.gitignore`).
Значения `CRAFT_API_BASE` (connect-link REST base с токеном) и `CRAFT_LINKS_STORE` (block-ID
дока с индексом бэклинков) — у Влада, в git их нет.

```dotenv
export CRAFT_API_BASE=https://connect.craft.do/links/XXXX/api/v1   # СЕКРЕТ (токен в URL)
export CRAFT_LINKS_STORE=<block-id>
export CRAFT_SYNC_BUILD=1                                           # SessionStart-хук соберёт craft-sync
export DISABLE_AUTOUPDATER=1                                        # бинарник claude без фоновых процессов
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1                   # (телеметрия/error-reporting/апдейтер off)
```

Перед ручными прогонами: `source ./.env`.

## Локальные пути в eval-конфигах (держать вне git)

[evals/mcp-config.json](evals/mcp-config.json) и `evals/mcp-config-sandbox.json` содержат
абсолютные пути под облако (`/opt/node22/bin/node`, `/home/user/craft/...`). Локально
перепиши `command` на путь к своему node ≥22, а `args` — на абсолютный путь к
`evals/mock-craft-*.js` в этой репе, и убери файлы из-под коммита:

```bash
git update-index --skip-worktree evals/mcp-config.json evals/mcp-config-sandbox.json
```

## Проверка готовности

```bash
craft-sync --help                 # бинарник собран хуком и на PATH
bash tests/run.sh                 # тесты хуков — exit 0 (42/42)
source ./.env
bash evals/check-structure.sh     # read-only структурный страж «Продукты»
bash evals/run-matrix.sh          # L1 mock: Продукты (по умолч. haiku)
bash evals/run-dates.sh           # L1 mock: срок vs дедлайн (герметичный, fixtures)
bash evals/run-e2e.sh             # L2 e2e — только когда L1 зелёные
```

> Дефолтная модель эвалов — haiku, слабая на нюансных правилах: `run-dates` на haiku
> может дать 0/5, на sonnet — проходит (`bash evals/run-dates.sh claude-sonnet-5`).
> Зелёный результат — признак способной модели, а не только корректного окружения.
