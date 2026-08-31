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
//   project   — только в этой репе (краевые craft-хуки: роутер, инцидент, сборка);
//   universal — только в пользовательском слое, в сессиях ЧУЖИХ проектов
//               (инжекторы правил: в craft-репо их работу делает роутер);
//   both      — в обоих.
//
// МАТЧЕР сверяется с каноническим маршрутом действия ЦЕЛИКОМ. Пустой матчер
// означает «на любое событие этого типа». Нативные имена знает только адаптер.
export const TABLE = {
  'session.start': [
    {
      hooks: [
        'client-inject-context',
        'craft-sync-local-main',
        'craft-build-sync',
        'craft-inject-incident',
        'universal-inject-instincts',
      ],
      scope: 'client',
    },
    { hooks: ['craft-sync-local-main', 'craft-build-sync'], scope: 'project' },
    { hooks: ['craft-inject-router', 'craft-inject-incident'], scope: 'project' },
    {
      hooks: ['universal-inject-behavior-rules', 'universal-inject-code-rules', 'universal-inject-instincts'],
      scope: 'universal',
    },
    // Клиентский вход раскладывается ДО карты возможностей: карта сообщает, что в
    // сессии доступно, и к этому моменту вход уже должен лежать на месте.
    { hooks: ['universal-cache-gate-exempt-scope', 'client-auth', 'universal-env-capabilities'], scope: 'both' },
    { hooks: ['universal-session-anchor'], scope: 'both' },
  ],

  'user.prompt': [
    {
      hooks: [
        'universal-session-anchor',
        'universal-detect-incident',
        'universal-plan-gate-reset',
        'universal-mark-plan-critic',
        'universal-sync-system',
        'universal-classifier-notifications',
      ],
      scope: 'both',
    },
  ],

  // Some harnesses emit native subagent lifecycle events outside tool events.
  // Start receives the same live Craft context as the parent; Stop is reduced
  // to the universal invocation shape so plan-critic accounting stays in core.
  'agent.start': [
    { hooks: ['client-inject-context'], scope: 'client' },
  ],

  'agent.stop': [
    { hooks: ['universal-mark-plan-critic'], scope: 'client' },
  ],

  'action.before': [
    { matcher: 'agent.invoke', hooks: ['universal-guard-critic-plateau'], scope: 'both' },
    {
      matcher: 'plan.submit',
      hooks: [
        'universal-guard-plan-critic',
        'universal-guard-plan-delta',
        'universal-guard-plan-service-turn',
      ],
      scope: 'both',
    },
    {
      matcher: 'data.mutate',
      hooks: ['craft-guard-markdown'],
      scope: 'project',
    },
    { matcher: 'data.mutate', hooks: ['craft-guard-markdown'], scope: 'client' },
    {
      matcher: 'data.mutate',
      hooks: ['universal-fact-gate'],
      scope: 'both',
    },
    {
      matcher: 'file.mutate',
      hooks: ['craft-guard-plan-hygiene'],
      scope: 'project',
    },
    {
      matcher: 'file.mutate|file.patch',
      hooks: ['craft-guard-plan-hygiene'],
      scope: 'client',
    },
    {
      matcher: 'command.run',
      hooks: [
        'universal-sleep-waiter-guard',
        'universal-kill-by-name-guard',
        'universal-block-no-verify',
        'universal-fact-gate',
      ],
      scope: 'both',
    },
    { matcher: 'file.mutate|file.patch', hooks: ['universal-config-protection'], scope: 'both' },
    { matcher: 'read', hooks: ['universal-eval-materials-guard'], scope: 'both' },
    // Гейт слушает ВСЕ вызовы, а не три набора инструментов: своя команда записи
    // есть у любого стороннего сервера, и перечислением их не закрыть. Сам гейт
    // пропускает то, про что видно, что оно только читает; матчер здесь широкий
    // намеренно — решение принимает хук, а не список имён.
    { hooks: ['universal-guard-plan-gate'], scope: 'both' },
  ],

  'action.after': [
    { matcher: 'session.question', hooks: ['universal-session-anchor', 'universal-plan-gate-button'], scope: 'both' },
    {
      matcher: 'plan.submit',
      hooks: ['universal-plan-gate-approve', 'universal-guard-plan-delta'],
      scope: 'both',
    },
    { matcher: 'agent.invoke', hooks: ['universal-mark-plan-critic'], scope: 'both' },
    { matcher: 'file.mutate|file.patch', hooks: ['universal-mark-plan-file'], scope: 'both' },
    { matcher: 'file.mutate|file.patch', hooks: ['universal-track-edited-files'], scope: 'both' },
    { hooks: ['universal-observe-buffer'], scope: 'both' },
  ],

  'action.failure': [
    { matcher: 'plan.submit', hooks: ['universal-guard-plan-exit-failure'], scope: 'both' },
  ],

  'turn.stop': [
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

  'context.compact': [
    { hooks: ['universal-pre-compact'], scope: 'both' },
  ],
};

// События, на которые ставится сама регистрация диспетчера.
export const EVENTS = Object.keys(TABLE);

// Совпадение матчера с именем инструмента. Пустой матчер — «всегда».
function matches(matcher, route) {
  if (!matcher) return true;
  try {
    return new RegExp(`^(?:${matcher})$`).test(route);
  } catch {
    return false; // нечитаемый матчер никого не зовёт, а не всех подряд
  }
}

// Имена хуков для события и инструмента, в порядке таблицы и без повторов.
export function hooksFor(event, route, scope) {
  const seen = new Set();
  const out = [];
  for (const entry of TABLE[event] || []) {
    if (entry.scope !== 'both' && entry.scope !== scope) continue;
    if (!matches(entry.matcher, route)) continue;
    for (const name of entry.hooks) {
      if (seen.has(name)) continue;
      seen.add(name);
      out.push(name);
    }
  }
  return out;
}
