// Класс причины отказа: короткое имя, по которому судят сводки. Живёт отдельно,
// потому что его считают двое — тот, кто решает (пишет класс в журнал решений), и
// свёртка метрик. Текст причины дальше класса не идёт никуда: в нём куски работы
// Влада, а журнал переживает сессию.
//
// Текст нужен только план-гейту и дельте: у остальных хуков один исход на файл.

// Короткое имя хука: без контурного префикса.
export function hookShort(name) {
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
