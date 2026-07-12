# CLAUDE.md

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
