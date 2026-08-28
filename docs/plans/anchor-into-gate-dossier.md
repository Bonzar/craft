# Досье фактов: одна блокировка вместо двух гвардов

Служебная опора плана: что прочитано и проверено, дословные формулировки, следствия. План на этих фактах стоит, но их не показывает.

## Дословная формулировка Влада
- «Но я хочу чтобы блокировал именно план гейт, с подключенным правилом про якорь, агент судящей о необходимости блокировки – один»
- ранее: «разрешенное не должно быть одним списком с гвардом плана? может вообще не семантику его перевести, и как плангин подключать в план гейт, что запись блочится, план гейтом ели нет целей и с нашим плагином, если нет якоря»
- ранее: «Кодекс должен подключаться до гварда»

## Прочитано свежим чтением (28.08.2026)
- .claude/hooks/universal-session-anchor.js целиком: три роли (SessionStart-директива, PostToolUse-приём тапа, PreToolUse-гвард). Гвард судит: craft_write → отказ; file edit → по isEphemeral/gitEphemeral; Bash → classifyCommand + правило «все цели временные».
- .claude/hooks/universal-guard-plan-gate.js целиком: все поверхности (file edit, Bash, прочие инструменты, craft_write) сходятся в одну функцию coverCheck. Bash-ветка: bashWriteTargets → отсев эфемерных → нет целей → выход. craft_write: exempt-scope проверяется ДО coverCheck.
- .claude/hooks/lib/write-targets.js: bashWriteTargets ловит перенаправление, tee, sed/perl -i, cp/mv, запись из интерпретатора. `rm` НЕ ловит — проверено чтением pieceTargets/interpreterTargets.
- .claude/hooks/dispatch-table.js: session-anchor стоит в PreToolUse трижды (craft_write, Write|Edit|MultiEdit|NotebookEdit, Bash), в SessionStart и в PostToolUse AskUserQuestion.
- grep по репо: единственный потребитель classifyCommand — universal-session-anchor.js (плюс собственный юнит-тест). vendor/read-only-rules.json описан в .claude/VENDORED-SKILLS.md двумя строками (vscode, safecmd).
- tests/run.js: SESSION_ANCHOR_STATE указывает на общий временный файл (s.anchor), чистится в cleanState. Сеятели registry_seed и codex_seed — образец для anchor_seed.
- tests/hooks/session-anchor.jsonl: 24 кейса, из них про PreToolUse-гвард — 16.
- tests/hooks/plan-gate.jsonl: кейсы с ожиданием allow при непустом реестре идут через setup plan-gate-approve; PLAN_CLASSIFIER_CMD подменяется моком.

## Следствия, установленные чтением, не догадкой
- После переезда `codex exec …` без целей записи проходит до якоря сам собой — старые юниты 1 и 2 прежнего плана (список разрешённых форм codex, предикаты `[ ]`/`test`) становятся не нужны.
- Регресс: `rm README.md` до якоря сейчас блокируется гвардом, после переезда — нет (bashWriteTargets про rm не знает). Отсюда юнит 2.
- Предодобренная зона Craft (Продукты) проходит план-гейт до coverCheck — если правило якоря звать только из coverCheck, запись в зону пойдёт без якоря. Отсюда решение звать правило раньше exempt-scope.
- Кейсы plan-gate.jsonl без засеянного якоря станут проверять не то, что заявляют (deny придёт от якоря). Отсюда anchor_seed на всех кейсах гейта.

## Правило базы, требующее правки
Роутер → подстраница «🎯 Задача-якорь сессии» (block eba151a5-ba0e-f173-3eb3-e4b65a8d95ce), абзац «Пока Влад не ответил, агент ждёт…» описывает старую семантику («команды, про которые видно, что они лишь читают»). Правка внутри папки «Память для агента» → идёт через SKILL «Обслуживание памяти».
