# tests/ — регресс-тесты bash-хуков

Тест-набор для хуков в `.claude/hooks/`: подаёт заготовленное событие на stdin
хука и проверяет исход. Защищает хуки от регресса. Зависимости — только `bash` + `jq`.

## Запуск

    bash tests/run.sh

Exit 0 — все кейсы зелёные и каждый исход каждого хука покрыт. Exit 1 — падение
или непокрытый исход (детали печатаются). Exit 2 — нет `jq` / нет файлов кейсов.
CI гоняет это на push и pull_request (`.github/workflows/hooks-tests.yml`).

## Формат кейса

`tests/hooks/<hook>.jsonl`, по одному JSON-объекту на строку:

    {"name":"…","hook":"detect-incident","expect":"inject","input":{"prompt":"…"}}

- `hook` — `guard-craft-markdown` | `guard-plan-hygiene` | `detect-incident` | `guard-plan-gate`.
- `input` — полный JSON события, как его подаёт Claude Code хуку на stdin.
- `expect` — `deny` (stdout с `permissionDecision:"deny"`) · `allow` (хук не заблокировал)
  · `inject` (stdout с `СИГНАЛ ИНЦИДЕНТА`) · `silent` (пустой stdout).
- `env` (опц.) — переменные окружения для вызова, напр. `{"CRAFT_AUTONOMOUS":"1"}`.
- `setup` (опц.) — хуки, прогоняемые до целевого, чтобы выставить состояние: напр.
  `["plan-gate-approve"]` ставит метку одобренного плана, `["plan-gate-reset"]` гасит.
  Метка план-гейта у каждого кейса своя (временный файл) — кейсы герметичны.

## Добавить кейс

Допиши строку в нужный `tests/hooks/<hook>.jsonl` и запусти `bash tests/run.sh`.
Что именно покрыто (включая пограничные случаи) — видно из полей `name` в этих
файлах; это единственный источник, здесь он не дублируется. Меняешь хук — сначала
обнови кейс, потом код.
