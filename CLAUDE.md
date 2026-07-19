# CLAUDE.md

## Роутер «Память для Claude» (авто-инжект)

Ниже импортируется полный текст роутера заметочной базы Craft. Файл
генерируется SessionStart-хуком `.claude/hooks/inject-craft-router.sh`
(живая версия из Craft на момент старта сессии); импорт через CLAUDE.md
нужен потому, что stdout хуков обрезается на 10 000 символах, а импорты —
нет. Если файла нет (первый запуск без сети) — импорт молча пропускается,
тогда роутер нужно прочитать из Craft (`blocks get` корня с `--depth -1`).

@.claude/craft-router-context.md

## craft-sync: первый старт в сессии

Бинарник в контейнере не предустановлен — перед первым использованием собрать
хуком: `bash .claude/hooks/build-craft-sync.sh --force` (кладёт в
`~/.local/bin/craft-sync`). Нужные env из настроек окружения:
`CRAFT_API_BASE` — базовый URL connect-API, `CRAFT_LINKS_STORE` — block-ID
Craft-дока с индексом обратных ссылок. Правила базы в Craft вызывают
craft-sync как готовый инструмент — данные о сборке и env живут только здесь
и в `craft-sync/README.md` (там же все режимы и флаги).

## Уроки для агентов в облачном окружении (Claude Code on the web)

### Headless Chromium за агент-прокси: ERR_CONNECTION_RESET — это TLS, а не сеть

В облачных сессиях весь исходящий HTTPS идёт через MITM агент-прокси
(`$HTTPS_PROXY`, CA: `/root/.ccr/ca-bundle.crt`). Симптомы и решение:

- `curl` через прокси работает, а Chromium/Playwright на **любом** сайте
  получает `net::ERR_CONNECTION_RESET` — прокси обрывает TLS 1.3-хендшейк
  Chromium (curl договаривается нормально, поэтому сеть «работает»).
- Не помогает: `--disable-features=PostQuantumKyber,X25519MLKEM768,
  EncryptedClientHello,UseMLKEM`, `--disable-quic`, `--ignore-certificate-errors`.
- **Помогает: `--ssl-version-max=tls1.2`** + `ignoreHTTPSErrors: true` в контексте.

Рабочая конфигурация Playwright:

```js
const browser = await chromium.launch({
  executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', // не качать заново
  proxy: { server: process.env.HTTPS_PROXY },   // Chromium НЕ читает env-прокси сам
  args: ['--ssl-version-max=tls1.2'],
});
const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
```

Диагностика по шагам, если снова упрётся: `curl -sS "$HTTPS_PROXY/__agentproxy/status"`
(поле `recentRelayFailures`), затем ручной CONNECT питоном, затем
`--ssl-version-max=tls1.2` — если заработало, дело в TLS-хендшейке.

### Connect-API видит меньше, чем MCP: расхождение доступа — сигнал Владу, не повод для обхода

Craft доступен агенту двумя путями с разной областью видимости: MCP-сервер
Craft видит всё пространство, а connect-ссылка (`$CRAFT_API_BASE`, ей ходит
SessionStart-хук инжекта роутера) — только явно расшаренный набор документов.

- Ссылку на документ вне шаринга connect-API рендерит в markdown как
  `[текст](invalid:out_of_scope)`, а прямой GET по его ID отвечает 403 —
  восстановить цель из ответа API невозможно.
- Обнаружил такое расхождение (или иную причину на стороне Влада) — не
  строить обходное решение (статические карты ID в хуке и подобные костыли),
  а подсветить Владу причину и дать список документов для добавления в шаринг
  connect-ссылки. Обход — только после явного решения Влада.
- Проверка после расширения шаринга: перезапустить хук и убедиться, что в
  `.claude/craft-router-context.md` не осталось `invalid:out_of_scope`.

### Текст перед tool-вызовом Влад не видит

Текст хода, написанный до вызова инструмента (в том числе перед
AskUserQuestion), в чат не доходит — Влад видит только сам вопрос и
финальное сообщение после всех tool-вызовов. Деливерабл (список, вывод,
ссылки) — всегда в финальном сообщении; перед блокирующим вопросом не
оставлять в тексте ничего, что Влад должен прочитать.

### Прочие грабли той же сессии

- `pkill -f <паттерн>` убивает и собственную bash-команду, если паттерн
  встречается в её тексте (exit 144). Убивать только по PID из pid-файла,
  и никогда не задевать процесс `environment-manager`.
- Playwright установлен глобально в `/opt/node22/lib/node_modules`; для ESM
  импортировать по абсолютному пути — `NODE_PATH` для ESM не работает.
- Букинг-виджеты (TravelLine, uhotels) живут в iframe: `page.evaluate` в них
  не достаёт, нужен обход `page.frames()` и клики внутри фрейма.
- Яндекс-поиск в headless из этого окружения работает без капчи,
  Google и DuckDuckGo — нет.
