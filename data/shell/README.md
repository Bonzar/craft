# Данные разбора команд оболочки

Списки, по которым модули судят, что команда делает с миром. Здесь только
данные: кода, который их читает, в этом каталоге нет.

- `read-only-rules.json` — что считается ЧТЕНИЕМ: списки читающих команд, их
  читающих подкоманд, запрещённых ключей, обёрток запуска, оболочек-обёрток,
  пустых приёмников вывода и заведомо пишущих команд.
- `write-targets-cases.json` — кейсы разбора команд: команда, ожидаемый вердикт
  и причина. Снято с тестов снятого слоя хуков, чтобы поведение разбора не
  пришлось выводить заново.

## Вендоренные данные

Порядок тот же, что у вендоренных скиллов (`.claude/VENDORED-SKILLS.md`): копия
лицензии рядом, снимок по коммиту или версии, отклонения названы. Разница одна —
списки команд не копируются дословно: чужие наборы отвечают на вопрос «безопасно
ли выполнить без спроса», а нам нужно «только ли читает», и в них живут команды,
меняющие состояние. Поэтому `read-only-rules.json` — производный файл, собранный
отбором, и обновление источника означает пересборку, а не замену.

| Кусок | Источник | Upstream-путь | Коммит или версия | Дата | Лицензия | Отклонения |
|---|---|---|---|---|---|---|
| `read-only-rules.json` (основа) | github.com/microsoft/vscode | `src/vs/workbench/contrib/terminalContrib/chatAgentTools/common/terminalChatAgentToolsConfiguration.ts`, `src/vs/platform/terminal/common/autoApprove/gitAutoApproveRules.ts` | `79ee223375d9e150b2b84cd59815ada7c5871c21` | 2026-08-26 | MIT | не копия: строки прошли отбор под предикат «только читает», выброшены `npm ci`, `yarn install --frozen-lockfile`, `pnpm install --frozen-lockfile`; PowerShell-часть не взята; формат свой |
| `read-only-rules.json` (подкоманды) | github.com/AnswerDotAI/safecmd | `safecmd/core.py`, строка `default_cfg` | `6ae261916a2559fcab7001058bdbd3024cd59aef` | 2026-08-26 | Apache-2.0 | не копия: из `ok_cmds` выброшено всё, что меняет состояние — `git add`, `git commit`, `git checkout`, `git switch`, `git fetch`, `npm install`, `npm pack`, `docker pull`, `docker build`, `aws s3 cp`, `unzip`, `gunzip`, `bunzip2`, `unrar`, `nbdev_export`, `nbdev_clean` |

Копии лицензий обоих источников — `LICENSE.microsoft-vscode` и
`LICENSE.AnswerDotAI-safecmd` рядом с файлом.

Обновление: заново пройти отбором по источнику. Заменой файла это сделать
нельзя — иначе в наш предикат протечёт чужое «безопасно» вместо нашего
«читает». После пересборки обновить коммит и дату в таблице.

## Происхождение кейсов

`write-targets-cases.json` — не вендоренный файл: кейсы сняты со своих тестов
снятого слоя JS-хуков (`tests/unit/write-targets-bash.test.mjs`,
`tests/unit/write-targets.test.mjs`, `tests/unit/write-targets-git.test.mjs`).
Поле `expected` — ожидаемый вердикт своими словами, `why` — причина, ради
которой кейс заводился. Кода разбора рядом нет: он пишется заново.
