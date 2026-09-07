// Свёртка журнала сессии в одну сводку. Чистая функция над записями: ни диска,
// ни окружения, ни имён инструментов — только признаки, которые проставила
// обёртка (см. контракт записи в шапке universal-metrics.js). Имена событий
// берутся из общего словаря (lib/event.js) — харнеса свёртка не знает.
//
// summarize(записи, {sid, now}) → объект сводки. Состав полей виден в blank():
// это и есть описание того, что уезжает в хранение.
//
// Каждая метрика считается СВОИМ проходом по записям: у одного прохода на все
// одиннадцать метрик тело не помещалось на экран, и правка одной метрики
// требовала перечитывать все.
import { EVENTS } from './event.js';

// «Сразу после реплики» — это ход отказа или следующий за ним. Дальше уже новая
// работа, и правильный отказ, снятый через несколько ходов новым планом, ложным
// не считается.
const DENY_WINDOW_TURNS = 1;

// Исход, которого мы не знаем. Отдельное слово, а не пустая строка и не `allow`:
// «не доехало» и «прошёл» — разные вещи, и вторая тут была бы выгодной ложью.
const UNKNOWN = 'unknown';

// Время записи в миллисекундах; нет времени — NaN.
const ms = (ts) => Date.parse(ts);

// Последнее непустое значение по каждому ходу: у хода записей бывает несколько,
// и решает последняя.
function byTurn(records, pick) {
  const out = new Map();
  for (const r of records) {
    const value = pick(r);
    if (value !== undefined) out.set(r.turn, value);
  }
  return out;
}

function blank(sid, now) {
  return {
    sid, harness: '', repo: '', started_at: '', ended_at: new Date(now).toISOString(),
    turns: 0,
    tokens: { input: 0, output: 0, cache_read: 0, cache_create: 0 },
    tokens_first_turn: null,
    model_calls: { count: 0, ms: 0, by_mode: {} },
    denies: { total: 0, by_class: {} },
    false_denies: 0,
    plan: { shown: 0, bounced: 0, approved: 0 },
    incidents: { detected: 0, skill_called: 0, share: null },
    unknown_events: 0,
    stop_blocks: {},
    tool_errors: 0,
    first_edit_ms: null,
    outcome: { note_writes: 0, pushed: false },
    signals: {
      prompt_repeats: 0,
      reinstructions: 0,
      call_repeats: 0,
      stage_repeats: 0,
      turns_without_progress: 0,
      error_streak_max: 0,
    },
  };
}

// Запись pre того же вызова: у post своего входа нет, признаки лежат на pre.
function preById(records) {
  const pres = new Map();
  for (const r of records) if (r.kind === 'pre' && r.id) pres.set(r.id, r);
  return pres;
}

// Удавшийся вызов: запись исхода без ошибки. Записи `fail` удавшимися не бывают
// по определению, поэтому проверяется одно — `post` без пометки ошибки.
const succeeded = (r) => r.kind === 'post' && r.error !== true;

// --- метрики ------------------------------------------------------------------

function fillSession(s, records) {
  for (const r of records) {
    if (r.kind !== 'session') continue;
    // Стартов бывает несколько (компакт, возобновление): поздние старты начало
    // сессии не двигают, но метку харнеса и репозитория уточняют.
    s.harness = r.harness || s.harness;
    s.repo = r.repo || s.repo;
    s.sid = s.sid || r.sid || '';
  }
  const first = records.map((r) => ms(r.ts)).find(Number.isFinite);
  s.started_at = Number.isFinite(first) ? new Date(first).toISOString() : '';
}

// Ходы — по числу РАЗЛИЧНЫХ номеров. Нулевой ход ходом не является: реплики
// ещё не было, это Stop служебного вызова до начала разговора.
function fillTurns(s, records) {
  const seen = new Set();
  for (const r of records) if (Number.isFinite(r.turn) && r.turn > 0) seen.add(r.turn);
  s.turns = seen.size;
}

function fillTokens(s, records) {
  let firstTurn = null;
  for (const r of records) {
    if (r.kind !== 'stop') continue;
    const u = r.usage && typeof r.usage === 'object' ? r.usage : {};
    for (const key of Object.keys(s.tokens)) s.tokens[key] += Number(u[key]) || 0;
    // Токены первого хода — сумма ВСЕХ его Stop: у хода их бывает несколько, и
    // у каждого своя доля usage. Нулевой ход первым НЕ становится: Stop
    // служебного вызова до первой реплики дал бы нулевые токены первого хода.
    if (r.turn <= 0) continue;
    if (s.tokens_first_turn === null) {
      firstTurn = r.turn;
      s.tokens_first_turn = { input: 0, output: 0, cache_read: 0, cache_create: 0 };
    }
    if (firstTurn === r.turn) {
      for (const key of Object.keys(s.tokens_first_turn)) s.tokens_first_turn[key] += Number(u[key]) || 0;
    }
  }
}

function fillModelCalls(s, records) {
  for (const r of records) {
    if (r.kind !== 'model') continue;
    s.model_calls.count += 1;
    s.model_calls.ms += Number(r.ms) || 0;
    const mode = r.mode || 'unknown';
    const m = s.model_calls.by_mode[mode] || { count: 0, ms: 0 };
    m.count += 1;
    m.ms += Number(r.ms) || 0;
    s.model_calls.by_mode[mode] = m;
  }
}

function fillDenies(s, records) {
  for (const r of records) {
    if (r.kind !== 'pre' || r.decision !== 'deny') continue;
    s.denies.total += 1;
    const cls = r.class || 'unknown';
    s.denies.by_class[cls] = (s.denies.by_class[cls] || 0) + 1;
  }
}

// Ложный отказ — «deny, а затем ТОТ ЖЕ вызов прошёл сразу после вмешательства
// Влада»: реплика открывает новый ход, кнопка отвечает внутри текущего, поэтому
// окно — ход отказа и следующий за ним (DENY_WINDOW_TURNS), а не «тот же ход».
// Отказ запоминается по хешу вызова; замок снимается КАЖДЫМ вмешательством
// заново — между отказом и повтором Влад успевает и ответить кнопкой, и
// написать. Дальше окна отказ из ожидания уходит: это уже новая работа.
function fillFalseDenies(s, records, pres) {
  const pending = new Map();
  const unlock = (turn) => {
    for (const [hash, p] of pending) {
      if (turn - p.denyTurn > DENY_WINDOW_TURNS) pending.delete(hash);
      else p.unlockTurn = turn;
    }
  };
  for (const r of records) {
    if (r.kind === 'prompt') {
      unlock(r.turn);
    } else if (r.kind === 'pre' && r.decision === 'deny') {
      if (r.h) pending.set(r.h, { unlockTurn: null, denyTurn: r.turn });
    } else if (r.kind === 'pre' && r.decision === 'allow') {
      if (r.h && pending.has(r.h)) {
        const p = pending.get(r.h);
        if (p.unlockTurn !== null && p.unlockTurn === r.turn) s.false_denies += 1;
        pending.delete(r.h);
      }
    } else if (succeeded(r)) {
      const pre = r.id ? pres.get(r.id) : null;
      if (pre && pre.question === true) unlock(r.turn);
    }
  }
}

function fillPlan(s, records, pres) {
  for (const r of records) {
    if (r.kind === 'pre' && r.plan === true) {
      if (r.decision === 'deny') s.plan.bounced += 1;
      else if (r.decision === 'allow') s.plan.shown += 1;
    } else if (succeeded(r)) {
      const pre = r.id ? pres.get(r.id) : null;
      if (pre && pre.plan === true) s.plan.approved += 1;
    }
  }
}

// Доля разборов: сколько ходов, в которых сработал признак инцидента, кончились
// вызовом скилла разбора. Скилл засчитывается только УДАВШИМСЯ вызовом.
function fillIncidents(s, records, pres) {
  const detected = new Set();
  const analyzed = new Set();
  for (const r of records) {
    if (r.kind === 'prompt' && r.incident === true) detected.add(r.turn);
    else if (succeeded(r)) {
      const pre = r.id ? pres.get(r.id) : null;
      // Разбор опознаётся ПРИЗНАКОМ от обёртки: имена скиллов знает она, а не
      // свёртка — иначе список имён жил бы в двух местах и разъезжался.
      if (pre && pre.incident_skill === true) analyzed.add(r.turn);
    }
  }
  s.incidents.detected = detected.size;
  s.incidents.skill_called = [...detected].filter((turn) => analyzed.has(turn)).length;
  s.incidents.share = detected.size ? s.incidents.skill_called / detected.size : null;
}

function fillStopBlocks(s, records) {
  for (const r of records) {
    if (r.kind === 'stop' && r.blocked_by) {
      s.stop_blocks[r.blocked_by] = (s.stop_blocks[r.blocked_by] || 0) + 1;
    }
  }
}

function fillToolErrors(s, records) {
  for (const r of records) {
    if ((r.kind === 'post' || r.kind === 'fail') && !succeeded(r)) s.tool_errors += 1;
  }
}

// Исход сессии: сколько записей ушло в базу заметок и был ли пуш. Инструмент,
// которым эта база ведётся, свёртке неизвестен: признак ставит обёртка.
function fillOutcome(s, records, pres) {
  for (const r of records) {
    if (!succeeded(r)) continue;
    const pre = r.id ? pres.get(r.id) : null;
    if (!pre) continue;
    if (pre.note_write === true) s.outcome.note_writes += 1;
    if (pre.push === true) s.outcome.pushed = true;
  }
}

// Время до первой правки — от начала сессии до первого удавшегося правящего
// вызова. Показ плана правкой не считается.
function fillFirstEdit(s, records, pres) {
  const started = records.map((r) => ms(r.ts)).find(Number.isFinite);
  if (!Number.isFinite(started)) return;
  for (const r of records) {
    if (!succeeded(r)) continue;
    const pre = r.id ? pres.get(r.id) : null;
    if (!pre || pre.edit !== true) continue;
    const t = ms(r.ts);
    if (!Number.isFinite(t)) continue;
    s.first_edit_ms = t - started;
    return;
  }
}

function fillSignals(s, records) {
  let streak = 0;
  for (const r of records) {
    if (r.kind === 'prompt') {
      if (r.repeat === true) s.signals.prompt_repeats += 1;
      if (r.reinstruct === true) s.signals.reinstructions += 1;
      // Серия ошибок — про то, как агент бьётся ВНУТРИ хода: реплика Влада её
      // разрывает, иначе ошибки по обе стороны его вмешательства сложились бы
      // в одну серию, которой не было.
      streak = 0;
    } else if (r.kind === 'pre') {
      if (r.repeat_call === true) s.signals.call_repeats += 1;
      if (r.stage_repeat === true) s.signals.stage_repeats += 1;
    } else if (r.kind === 'post' || r.kind === 'fail') {
      streak = succeeded(r) ? 0 : streak + 1;
      if (streak > s.signals.error_streak_max) s.signals.error_streak_max = streak;
    }
  }
  // Ход без прогресса считается по ХОДУ, а не по записи Stop: у одного хода
  // записей Stop бывает несколько, и решает последняя. Нулевой ход в счёт не
  // идёт: до первой реплики хода не было.
  const noProgress = byTurn(records, (r) => (
    r.kind === 'stop' && typeof r.no_progress === 'boolean' && r.turn > 0
      ? r.no_progress : undefined));
  s.signals.turns_without_progress = [...noProgress.values()].filter(Boolean).length;
}

// Сшивка события с его решением. Наблюдатель записывает событие ДО решателей
// (иначе отказ обрывал бы цепочку до него), а решение, признак и замеры приходят
// отдельными строками — их переносит он же на следующем событии.
//
// Сшивают по ПАРЕ «ключ и событие». Ключ (lib/event-key.js) — идентификатор
// вызова, а у событий без него хеш сырых байтов события. Имя события у строки
// канала своё; у записи события им служит её ВИД — второго поля ради того же не
// заводится.
//
// Почему пара, а не ключ. Событие ДО вызова и событие ПОСЛЕ него несут ОДИН
// идентификатор вызова. По голому ключу решение, записанное после вызова, легло
// бы на запись до него, а замеры одного из этих двух событий гасили бы вопрос
// «доехали ли строки» у другого — то есть потерянный отказ читался бы как проход.
//
// Почему ещё и ПО ПОРЯДКУ. Пара тоже не уникальна: два байт-в-байт одинаковых
// события без идентификатора вызова (два конца хода подряд) дают один ключ, и
// различить их нечем — ни у диспетчера, ни у пакета нет ничего, кроме самих
// байтов события. Поэтому строки одного рода раздаются записям ПО ОЧЕРЕДИ: i-й
// записи пары — i-я строка. Иначе одна блокировка досталась бы обоим концам хода
// и посчиталась дважды. Там, где идентификатор вызова есть, запись в паре одна и
// очередь вырождается в «единственная строка единственной записи».
//
// Повтор переноса безвреден: лишние строки остаются без записи, а считается всё
// по ЗАПИСЯМ СОБЫТИЙ, которых по одной на событие.
//
// Своё поле записи, если оно есть, сильнее: у записей самого старого слоя исход
// стоит прямо в них. У записей слоя с номером появления он стоит в СТРОКЕ, и
// поэтому ключ читается в обеих эпохах (см. recordKey/lineKey ниже).
const EVENT_BY_KIND = Object.freeze({
  session: EVENTS.SESSION_START,
  prompt: EVENTS.PROMPT,
  pre: EVENTS.PRE_TOOL,
  post: EVENTS.POST_TOOL,
  fail: EVENTS.POST_TOOL_FAILURE,
  stop: EVENTS.STOP,
});

// Пара одной строкой: карты сравнивают ключи по значению, а не по полям.
const pairOf = (key, event) => `${key}\n${event}`;
// Ключ записи и ключ строки канала. Читаются ОБЕ эпохи: журнал переживает
// обновление слоя, а прежний слой писал номер появления процесса (`occ` у записи,
// `occurrence` у строки). Смотри только новое поле — у записей, сделанных до
// обновления, пара выйдет пустой, и отказ, лежащий в их строке, пропал бы совсем:
// ни в отказах, ни даже в числе событий без исхода. Именно эту потерю карточка и
// затевалась предотвратить.
const recordKey = (r) => r.key || r.occ || '';
const lineKey = (r) => r.key || r.occurrence || '';
// Пара ЗАПИСИ СОБЫТИЯ и пара СТРОКИ КАНАЛА. Пустая пара значит «этой строке не с
// чем сшиваться»: у записи нет ключа или её вид событием не является (вызов
// модели, пропуск, сводка), у строки канала не разобралось имя события.
const recordPair = (r) => (recordKey(r) && EVENT_BY_KIND[r.kind] ? pairOf(recordKey(r), EVENT_BY_KIND[r.kind]) : '');
const linePair = (r) => (lineKey(r) && r.event ? pairOf(lineKey(r), r.event) : '');

// Строки канала одной пары, разложенные по роду. Исключающие решения и
// дописанный контекст лежат ПОРОЗНЬ: `none` — это «я не решал», и затирать им
// отказ нельзя, в каком бы порядке строки ни легли. Замеры считаются числом —
// от них нужно только «сколько событий пары доказали доставку»; признаки
// считаются числом на каждое имя, потому что спрашивают у них ровно наличие.
function channels(records) {
  const byPair = new Map();
  const of = (pair) => {
    if (!byPair.has(pair)) byPair.set(pair, { firm: [], none: [], timings: 0, flags: new Map() });
    return byPair.get(pair);
  };
  for (const r of records) {
    const pair = linePair(r);
    if (!pair) continue;
    const ch = of(pair);
    // Доказательство доставки — именно ЗАМЕРЫ, а не любая строка канала: их
    // диспетчер кладёт на КАЖДОМ событии. Дописанный контекст пишет свою строку
    // с исходом `none`, и, считай её доказательством, потерянный на том же
    // событии отказ прочитался бы как проход.
    if (r.kind === 'timing') ch.timings += 1;
    else if (r.kind === 'decision') (r.outcome === 'none' ? ch.none : ch.firm).push(r);
    else if (r.kind === 'flag' && r.flag) ch.flags.set(r.flag, (ch.flags.get(r.flag) || 0) + 1);
  }
  return byPair;
}

function stitch(records) {
  const byPair = channels(records);
  const seen = new Map();
  return records.map((r) => {
    const pair = recordPair(r);
    if (!pair) return r;
    // Который раз эта пара встречается среди записей: им и выбирается строка.
    const nth = seen.get(pair) || 0;
    seen.set(pair, nth + 1);
    const ch = byPair.get(pair) || { firm: [], none: [], timings: 0, flags: new Map() };
    // Исключающее решение сильнее дописанного контекста, в каком бы порядке
    // строки ни легли.
    const firm = ch.firm[nth];
    const d = firm || ch.none[nth];
    // Строка решения найдена — она и есть факт, даже если канал потом сорвался.
    // Ничего не пришло — исход НЕИЗВЕСТЕН. Так честнее в обе стороны: и когда
    // канал сорвался посреди сессии, и на её ХВОСТЕ, где переносить решение уже
    // некому — последнее событие сессии своих строк не дождалось.
    //
    // Спрашивается это только у записей, сделанных под диспетчером (`disp`): вне
    // его замеров цепочки не бывает вовсе, и ждать нечего — там пустота значит
    // ровно то, чем была всегда, то есть проход.
    const unknown = !firm && r.disp === true && ch.timings <= nth;
    if (r.kind === 'pre') {
      // Строки решения нет, а прочие строки события доехали — значит решения не
      // было, то есть проход: молчащий гвард в журнал не пишет.
      // Исход `none` — это дописанный контекст, а не решение по вызову: инжектор
      // ничего не запрещал, значит вызов ПРОШЁЛ. Без этой строчки первый же
      // инжектор на событии до вызова тихо вычел бы вызов из ложных отказов и из
      // показов плана — они сравнивают ровно с `allow`.
      return {
        ...r,
        decision: r.decision || (firm ? firm.outcome : (unknown ? UNKNOWN : 'allow')),
        by: r.by || (firm ? firm.hook : ''),
        class: r.class || (firm ? firm.class || '' : ''),
      };
    }
    if (r.kind === 'stop') {
      // Поля нет вовсе, когда неизвестно: пустое `blocked_by` читалось бы как
      // «никто не блокировал».
      if (unknown && !r.blocked_by) return { ...r, unknown: true };
      return { ...r, blocked_by: r.blocked_by || (d && d.outcome === 'block' ? d.hook : '') };
    }
    if (r.kind === 'prompt') {
      // Признак инцидента ставит другой хук той же цепочки, и приходит он тем же
      // каналом. Не доехало — поля НЕТ: `false` здесь утверждало бы, что инцидента
      // не было, и роняло бы долю разборов ровно так же, как пустота роняла отказ.
      // ДОЕХАВШАЯ строка сильнее отметки о пропаже — то же правило, по которому
      // доехавшее решение важнее позднего обрыва. Доехавший признак не
      // выбрасывается вместе с пометкой «исход неизвестен»: реплика и правда
      // осталась без полного канала, но про инцидент мы уже знаем.
      const seenFlag = r.incident === true || (ch.flags.get('incident') || 0) > nth;
      if (unknown && !seenFlag) return { ...r, unknown: true };
      if (unknown) return { ...r, unknown: true, incident: true };
      return { ...r, incident: seenFlag };
    }
    return unknown ? { ...r, unknown: true } : r;
  });
}

// Сколько СОБЫТИЙ не доказали доставку своих строк — отсюда и имя поля: считаются
// любые записи, а не только вызовы и концы хода. Тем же каналом приходят признаки
// реплики, и потерянный признак инцидента роняет долю разборов так же тихо, как
// потерянный отказ ронял счёт отказов. Пока число ноль — остальные метрики считаны
// по полному материалу; выросло — видно, на сколько именно сводка неполна.
function fillUnknown(s, records) {
  s.unknown_events = records.filter((r) => r.unknown === true || r.decision === UNKNOWN).length;
}

export function summarize(raw, { sid = '', now = Date.now() } = {}) {
  const s = blank(sid, now);
  const records = stitch(raw);
  const pres = preById(records);
  fillSession(s, records);
  fillTurns(s, records);
  fillTokens(s, records);
  fillModelCalls(s, records);
  fillDenies(s, records);
  fillFalseDenies(s, records, pres);
  fillPlan(s, records, pres);
  fillIncidents(s, records, pres);
  fillStopBlocks(s, records);
  fillToolErrors(s, records);
  fillOutcome(s, records, pres);
  fillFirstEdit(s, records, pres);
  fillSignals(s, records);
  fillUnknown(s, records);
  return s;
}
