// Маршруты хуков: какое событие, при каком инструменте и в каком порядке зовёт
// какие хуки. Это ЕДИНСТВЕННОЕ место, где живёт состав слоя: раньше он был
// расписан дважды — в проектных настройках и в установщике, — и любая правка
// требовала не забыть про второй контур.
//
// Порядок внутри события значим: хуки идут сверху вниз, и первый же ответ
// с решением (запрет, вопрос, блокировка конца хода) обрывает цепочку — иначе
// в общий вывод легли бы два решения подряд.
//
// КОНТУР (scope) — где хук зарегистрирован:
//   project   — только в этой репе (инжекты роутера и инцидента, сборка);
//               префикс craft-* сюда не обязывает: гвард записи в базу стоит
//               в обоих контурах, потому что база доступна из любой сессии;
//   universal — только в пользовательском слое, в сессиях ЧУЖИХ проектов
//               (инжекторы правил: в craft-репо их работу делает роутер);
//   both      — в обоих.
//
// МАТЧЕР сверяется с именем инструмента ЦЕЛИКОМ, а не подстрокой: иначе `Bash`
// поймал бы и `BashOutput`. Пустой матчер означает «на любое событие этого типа».
//
// Хук метрик стоит ПЕРВЫМ, а не последним. Первое решение цепочку обрывает, и
// исключений из этого правила нет ни для кого: стоя последним, наблюдатель просто
// не звался бы на каждом отказе — а отказы и есть то, что он считает. Поэтому он
// записывает событие ДО решения, а само решение читает из журнала решений на
// СЛЕДУЮЩЕМ событии и переносит в свой журнал (см. шапку universal-metrics.js).
// На Stop за ним сразу идёт хранение: оно забирает сводку, которую метрики только
// что записали. В PreCompact метрик нет вовсе.
export const TABLE = {
  SessionStart: [
    { hooks: ['universal-metrics'], scope: 'both' },
    { hooks: ['craft-sync-local-main', 'craft-build-sync'], scope: 'project' },
    { hooks: ['craft-inject-router', 'craft-inject-incident'], scope: 'project' },
    {
      hooks: ['universal-inject-behavior-rules', 'universal-inject-code-rules', 'universal-inject-instincts'],
      scope: 'universal',
    },
    // Вход в codex раскладывается ДО карты возможностей: карта сообщает, что в
    // сессии доступно, и к этому моменту вход уже должен лежать на месте.
    { hooks: ['universal-cache-gate-exempt-scope', 'universal-codex-auth', 'universal-env-capabilities'], scope: 'both' },
    { hooks: ['universal-session-anchor'], scope: 'both' },
  ],

  UserPromptSubmit: [
    { hooks: ['universal-metrics'], scope: 'both' },
    {
      hooks: [
        'universal-detect-incident',
        'universal-plan-gate-reset',
        'universal-mark-plan-critic',
        'universal-sync-system',
      ],
      scope: 'both',
    },
  ],

  PreToolUse: [
    { hooks: ['universal-metrics'], scope: 'both' },
    { matcher: 'Task|Agent', hooks: ['universal-guard-critic-plateau'], scope: 'both' },
    {
      matcher: 'ExitPlanMode',
      hooks: [
        'universal-guard-plan-critic',
        'universal-guard-plan-delta',
        'universal-guard-plan-service-turn',
      ],
      scope: 'both',
    },
    {
      matcher: 'mcp__.*__craft_write',
      hooks: ['craft-guard-markdown'],
      scope: 'both',
    },
    {
      matcher: 'mcp__.*__craft_write',
      hooks: ['universal-session-anchor', 'universal-fact-gate'],
      scope: 'both',
    },
    {
      matcher: 'Write|Edit|MultiEdit|NotebookEdit',
      hooks: ['universal-session-anchor'],
      scope: 'both',
    },
    {
      matcher: 'Write|Edit|MultiEdit|NotebookEdit',
      hooks: ['craft-guard-plan-hygiene'],
      scope: 'project',
    },
    {
      matcher: 'Bash',
      hooks: [
        'universal-sleep-waiter-guard',
        'universal-kill-by-name-guard',
        'universal-block-no-verify',
        'universal-fact-gate',
        'universal-session-anchor',
      ],
      scope: 'both',
    },
    { matcher: 'Write|Edit|MultiEdit', hooks: ['universal-config-protection'], scope: 'both' },
    { matcher: 'Read|Grep|Glob', hooks: ['universal-eval-materials-guard'], scope: 'both' },
    // Гейт слушает ВСЕ вызовы, а не три набора инструментов: своя команда записи
    // есть у любого стороннего сервера, и перечислением их не закрыть. Сам гейт
    // пропускает то, про что видно, что оно только читает; матчер здесь широкий
    // намеренно — решение принимает хук, а не список имён.
    { hooks: ['universal-guard-plan-gate'], scope: 'both' },
  ],

  PostToolUse: [
    { hooks: ['universal-metrics'], scope: 'both' },
    { matcher: 'AskUserQuestion', hooks: ['universal-session-anchor', 'universal-plan-gate-button'], scope: 'both' },
    // Дельта стоит только ДО показа (PreToolUse): после одобрения план уже
    // лежит в реестре, и сравнивать его с реестром значило бы отбивать
    // собственное одобрение.
    { matcher: 'ExitPlanMode', hooks: ['universal-plan-gate-approve'], scope: 'both' },
    { matcher: 'Task|Agent|Workflow', hooks: ['universal-mark-plan-critic'], scope: 'both' },
    { matcher: 'Write|Edit|MultiEdit', hooks: ['universal-mark-plan-file'], scope: 'both' },
    { hooks: ['universal-observe-buffer'], scope: 'both' },
  ],

  PostToolUseFailure: [
    { hooks: ['universal-metrics'], scope: 'both' },
    { matcher: 'ExitPlanMode', hooks: ['universal-guard-plan-exit-failure'], scope: 'both' },
  ],

  Stop: [
    // Хранение идёт СРАЗУ ПОСЛЕ метрик: оно забирает сводку, которую те только что
    // записали, и обоим нужно отработать до того, как гвард конца хода решит
    // блокировать. Блокировка не теряется: она ложится строкой в журнал решений, а
    // за блокированным концом хода всегда идёт следующий — он её и посчитает.
    { hooks: ['universal-metrics', 'universal-metrics-store'], scope: 'both' },
    {
      hooks: [
        'universal-check-console-log',
        'universal-stop-quality-gate',
        'universal-instinct-flush',
        'universal-stop-routine-facts',
        'universal-stop-incident-closure',
        'universal-stop-relative-link',
        'universal-sync-system',
      ],
      scope: 'both',
    },
  ],

  PreCompact: [
    { hooks: ['universal-pre-compact'], scope: 'both' },
  ],
};

// События, на которые ставится сама регистрация диспетчера.
export const EVENTS = Object.keys(TABLE);

// Совпадение матчера с именем инструмента. Пустой матчер — «всегда».
function matches(matcher, tool) {
  if (!matcher) return true;
  try {
    return new RegExp(`^(?:${matcher})$`).test(tool);
  } catch {
    return false; // нечитаемый матчер никого не зовёт, а не всех подряд
  }
}

// Имена хуков для события и инструмента, в порядке таблицы и без повторов.
export function hooksFor(event, tool, scope) {
  const seen = new Set();
  const out = [];
  for (const entry of TABLE[event] || []) {
    if (entry.scope !== 'both' && entry.scope !== scope) continue;
    if (!matches(entry.matcher, tool)) continue;
    for (const name of entry.hooks) {
      if (seen.has(name)) continue;
      seen.add(name);
      out.push(name);
    }
  }
  return out;
}
