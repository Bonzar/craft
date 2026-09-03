// Свёртка журнала сессии в одну сводку. Чистая функция над записями: ни диска,
// ни окружения, ни имён инструментов — только признаки, которые проставила
// обёртка (см. контракт записи в шапке universal-metrics.js).
//
// summarize(записи, {sid, now}) → объект сводки. Состав полей виден в blank():
// это и есть описание того, что уезжает в хранение.
//
// Каждая метрика считается СВОИМ проходом по записям: у одного прохода на все
// одиннадцать метрик тело не помещалось на экран, и правка одной метрики
// требовала перечитывать все.
import { ms, byTurn } from './metrics-fold.js';

// Скиллы разбора инцидента: код-сессия и сессия над базой Craft. Список точный,
// потому что признак «скилл вызван» — это доля разборов, а не похожие имена.
const INCIDENT_SKILLS = new Set(['code-incident', 'craft-incident']);

// «Сразу после реплики» — это ход отказа или следующий за ним. Дальше уже новая
// работа, и правильный отказ, снятый через несколько ходов новым планом, ложным
// не считается.
const DENY_WINDOW_TURNS = 1;

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
    stop_blocks: {},
    tool_errors: 0,
    first_edit_ms: null,
    outcome: { craft_writes: 0, pushed: false },
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

const succeeded = (r) => (r.kind === 'post' || r.kind === 'fail')
  && !(r.kind === 'fail' || r.error === true);

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
    // у каждого своя доля usage.
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

// Ложный отказ — «deny, затем ТОТ ЖЕ вызов прошёл в том же ходе после реплики
// или кнопки». Отказ запоминается по хешу вызова; реплика (новый ход) или ответ
// кнопкой снимают с него замок, и снимают КАЖДЫЙ РАЗ заново — между отказом и
// повтором Влад успевает и ответить кнопкой, и написать. Из ожидания отказ
// уходит по своему возрасту (DENY_WINDOW_TURNS).
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
      if (pre && INCIDENT_SKILLS.has(String(pre.skill || ''))) analyzed.add(r.turn);
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

// Исход сессии: сколько записей ушло в Craft и был ли пуш.
function fillOutcome(s, records, pres) {
  for (const r of records) {
    if (!succeeded(r)) continue;
    const pre = r.id ? pres.get(r.id) : null;
    if (!pre) continue;
    if (pre.craft_write === true) s.outcome.craft_writes += 1;
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

export function summarize(records, { sid = '', now = Date.now() } = {}) {
  const s = blank(sid, now);
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
  return s;
}
