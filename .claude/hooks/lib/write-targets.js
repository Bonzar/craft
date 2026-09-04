// Что считать записью мира. Общий дом для гвардов, которые стоят на одних и тех
// же вызовах: план-гейт, гвард якоря сессии и метрики. Разъехавшиеся копии этих
// предикатов дали бы поверхность, где одно и то же место у одного гварда
// гейтится, а у другого нет, и заметно это стало бы только на живом прогоне.
//
// Имён инструментов здесь нет: область вызова и его форму приносит обёртка,
// разбор команды — адаптер интерпретатора (write-targets-bash.js).
//
// touchesWorld(область) → трогает ли вызов мир вообще.
// mutationOf(область, форма, {commandWrites, ignored}) → {status, mutates}.
// isEphemeral(путь) → путь, правка которого системным изменением не является.
// ignoredEphemeral(путь, предикат) → та же политика для путей, которые
//   репозиторий игнорирует; сам предикат приносит адаптер репозитория.

// Путь, правка которого системным изменением не является.
export function isEphemeral(fp) {
  // Файл плана пишет план-мод ДО того, как появится одобрение, — гейт на нём
  // заклинил бы само планирование.
  if (/\/plans\/.*\.md$/.test(fp)) return true;
  if (/^(\/tmp\/|\/private\/tmp\/|\/var\/folders\/|\/private\/var\/folders\/)/.test(fp)) return true;
  if (fp.includes('/scratchpad/')) return true;
  const tmp = process.env.TMPDIR;
  if (tmp && fp.startsWith(`${tmp.replace(/\/$/, '')}/`)) return true;
  // ~/.claude: харнесс непрерывно пишет туда служебное состояние (память,
  // сессии, задачи, тудушки) — оно обязано остаться свободным. Гейтятся только
  // СИСТЕМНЫЕ зоны: скиллы, хуки, агенты, правила, команды, воркфлоу, настройки.
  const home = process.env.HOME || '';
  if (home && fp.startsWith(`${home}/.claude/`)) {
    const rel = fp.slice(`${home}/.claude/`.length);
    const gated = /^(skills|hooks|agents|rules|commands|workflows)\//.test(rel)
      || ['settings.json', 'settings.local.json', 'craft.env'].includes(rel);
    return !gated;
  }
  return false;
}

// Игнорируемое репозиторием эфемерно (сборка, логи) для ЛЮБОГО инструмента
// записи, кроме путей внутри .claude/: там игнор не оправдание. Сам вопрос
// «игнорируется ли путь» задаёт адаптер репозитория: общая часть держит только
// политику. Предиката нет — ответ «нет»: молча считать игнорируемым всё подряд
// значило бы пропустить мимо гейта любую правку.
export function ignoredEphemeral(fp, ignored) {
  if (fp.startsWith('.claude/') || fp.includes('/.claude/')) return false;
  return typeof ignored === 'function' && ignored(fp) === true;
}

// --- трогает ли вызов мир -----------------------------------------------------

// Гейт стоит на правках МИРА: файлы, командная строка, база, внешние сервисы.
// Всё, что мир не трогает, — не его дело. ЧЕМ был вызов, общая часть не знает:
// область вызова приходит данными от обёртки, которая одна и знает имена
// инструментов харнеса (tool-flags-claude.js: toolScope):
//   {reads: true}   — вызов только читает, менять ему нечего;
//   {session: true} — вызов правит ход САМОЙ СЕССИИ (план, вопрос, список
//                     работы, расписание пробуждения), а не мир;
//   {}              — всё остальное.
export function touchesWorld(scope = {}) {
  return !(scope.reads === true || scope.session === true);
}

// --- мутирует ли вызов мир ------------------------------------------------------

// Мутирует ли вызов мир. Основание одно с план-гейтом (touchesWorld), а сверх
// него отсеивается эфемерное: правка в /tmp прогрессом хода не считается — так
// же, как `echo x > /tmp/...` у шелла, иначе два пути к одному и тому же
// расходились бы.
//
// ЧЕМ именно был вызов, говорит форма вызова от обёртки:
//   {kind: 'edit', path}    — правка содержимого по этому пути;
//   {kind: 'command', text} — команда интерпретатора;
//   ничего                  — всё прочее, что трогает мир.
//
// Команду разбирает АДАПТЕР интерпретатора: commandWrites(текст) → {mutates,
// targets}; вопрос «игнорирует ли путь репозиторий» — адаптер репозитория
// (ignored). Своего разбора у общей части нет, и адаптера ей никто не зашивает —
// нет адаптера, нет и ответа: {status: 'unsupported', capability:
// 'write-targets'}. Молчаливое «не мутирует» тут соврало бы про каждый ход, где
// работали шеллом.
export function mutationOf(scope = {}, call = {}, adapters = {}) {
  const durable = (fp) => Boolean(fp) && !isEphemeral(fp) && !ignoredEphemeral(fp, adapters.ignored);
  if (!touchesWorld(scope)) return { status: 'ok', mutates: false };
  if (call.kind === 'edit') return { status: 'ok', mutates: durable(call.path || '') };
  if (call.kind === 'command') {
    const cmd = String(call.text || '');
    if (!cmd) return { status: 'ok', mutates: false };
    const writes = adapters.commandWrites;
    if (!writes) return { status: 'unsupported', capability: 'write-targets' };
    const { mutates = false, targets = [] } = writes(cmd) || {};
    if (mutates) return { status: 'ok', mutates: true };
    return { status: 'ok', mutates: targets.some(durable) };
  }
  return { status: 'ok', mutates: true };
}

// ЦЕЛИ записи вызова, очищенные от эфемерного, — то есть ровно те пути, из-за
// которых mutationOf выше и сказал «менял». Живут здесь, а не у журнала: политика
// эфемерности одна, и вторая её копия дала бы вызов, помеченный записью, с
// пустым списком целей — расхождение, заметное только на живом прогоне.
//
// Пустой список записи не отменяет: `git commit` меняет репозиторий, ничего не
// перенаправляя, и целей у него нет вовсе. Про «менял ли» спрашивают mutationOf,
// а не длину этого списка.
export function durableTargets(call = {}, adapters = {}) {
  const durable = (fp) => Boolean(fp) && !isEphemeral(fp) && !ignoredEphemeral(fp, adapters.ignored);
  if (call.kind === 'edit') return [call.path || ''].filter(durable);
  if (call.kind === 'command') {
    const writes = adapters.commandWrites;
    // Адаптера нет — целей нет и назвать их нечем. Само «менял ли» на этом же
    // месте отвечает `unsupported`, и имя недостающего называет оно.
    if (!writes) return [];
    const { targets = [] } = writes(String(call.text || '')) || {};
    return targets.filter(durable);
  }
  return [];
}
