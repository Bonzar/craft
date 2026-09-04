// Класс причины отказа: короткое имя, по которому судят сводки.
//
// Живёт отдельно от метрик, потому что считает его ТОТ, КТО РЕШАЕТ: класс уходит в
// журнал решений вместе с решением, а текст причины дальше не идёт никуда — в нём
// куски работы Влада, а журнал переживает сессию. Свёртке метрик класс приходит
// готовым, поэтому импорта у неё нет.
//
// Текст нужен только план-гейту и дельте: у остальных хуков один исход на файл.

// Короткое имя хука: без контурного префикса.
function hookShort(name) {
  return String(name || '').replace(/^(universal|craft)-/, '');
}

const GATE_CLASSES = [
  ['реестр пуст', 'gate.empty'],
  ['запрещено твоей же записью', 'gate.forbidden'],
  ['не покрывает', 'gate.uncovered'],
  ['черновой', 'gate.draft'],
  ['не читается', 'gate.unreadable'],
  ['не дала решения', 'gate.no-verdict'],
];

export function reasonClass(hook, reason) {
  const short = hookShort(hook);
  const text = String(reason || '');
  if (short === 'guard-plan-gate') {
    const hit = GATE_CLASSES.find(([needle]) => text.includes(needle));
    return hit ? hit[1] : 'gate.other';
  }
  if (short === 'guard-plan-delta') {
    return text.includes('повторяет') ? 'delta.repeats' : 'delta.unavailable';
  }
  return short || 'unknown';
}
