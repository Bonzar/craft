// Сверка с реестром одобренного — последнее слово гейта.
//
// Одна сверка вместо трёх веток. Прежние спрашивали каждая про своё — план,
// окно разрешений, времянка, — и правка сверялась то с одним, то с другим.
//
// Пять исходов: запрещено записью, разрешено поверх запрета, покрыта задачей,
// черновое, не покрыта. Запрет проверяется раньше покрытия, а более позднее
// явное разрешение бьёт запрет.
//
// Нет решения — отказ, всегда: ни падение проверки, ни недоступность модели, ни
// смерть самого хука проходом не становятся. Мягкой деградации к путь-матчу
// больше нет — пути перестали быть основанием для решения.
import fs from 'node:fs';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { render } from '../registry.js';
import { classify, classifierPath } from '../classifier.js';

export const name = 'registry';

// Разрешающие исходы сверки. Остальное — отказ либо неответ.
const ALLOWING = /^(OVERRIDE|COVERED|DRAFT)/;

function tempFile(prefix) {
  const dir = process.env.TMPDIR || '/tmp';
  return path.join(dir, `${prefix}.${randomBytes(3).toString('hex')}`);
}

// Сверка — вызов модели, и на пограничной правке она отвечает по-разному на
// одном и том же входе: замер пяти прогонов подряд дал четыре отказа и одно
// «покрыта». Цена разброса ложится на Влада — он повторяет разрешение по два-три
// раза, пока не повезёт.
//
// Поэтому отказ не окончателен с первого раза: он переспрашивается, а разошлись
// ответы — решает третий голос. Проход остаётся проходом сразу: лишние вызовы
// тратятся только там, где иначе Влад теряет минуты. Отказ возвращается ПЕРВЫЙ
// из полученных — его причина уже написана про эту правку.
function steadyVerdict(classifier, view, desc) {
  const first = classify(classifier, 'cover', [view], desc);
  if (ALLOWING.test(first)) return first;

  const second = classify(classifier, 'cover', [view], desc);
  const agree = ALLOWING.test(first) === ALLOWING.test(second);
  if (agree) return first;

  const third = classify(classifier, 'cover', [view], desc);
  if (ALLOWING.test(third)) return ALLOWING.test(second) ? second : third;
  return ALLOWING.test(first) ? second : first;
}

export function run(ctx) {
  const goals = ctx.registry();

  // Пустой реестр модель не зовёт: одобренного нет, и спрашивать не о чем.
  if (!goals.length) {
    return {
      decision: 'deny',
      reason: 'Заблокировано план-гейтом: одобренного нет — реестр пуст. Покажи план и получи ок Влада, либо задай предметный вопрос-разрешение.',
    };
  }

  const desc = ctx.description();
  const view = tempFile('registry-view');
  try {
    fs.writeFileSync(view, render(goals));
  } catch {
    return {
      decision: 'deny',
      reason: 'Заблокировано план-гейтом: реестр одобренного не читается, сверить правку не с чем.',
    };
  }

  // Потолка сверке гейт не назначает: решения ждём, сколько бы оно ни заняло.
  // Своё число у неё есть — страховка от зависшего вызова, и живёт оно там же,
  // где сама проверка.
  const verdict = steadyVerdict(classifierPath(), view, desc);
  try { fs.rmSync(view, { force: true }); } catch { /* временный вид не убрался */ }

  if (verdict.startsWith('OVERRIDE')) return { decision: 'allow' };
  if (verdict.startsWith('FORBIDDEN')) {
    return {
      decision: 'deny',
      reason: `Заблокировано план-гейтом: это запрещено твоей же записью —${verdict.slice('FORBIDDEN:'.length)}. Пути дальше: сними запрет прямо в диалоге, либо покажи план с этой целью.`,
    };
  }
  if (verdict.startsWith('COVERED')) return { decision: 'allow' };
  if (verdict === 'DRAFT') {
    // Черновое проходит только при плановой цели в реестре: иначе в свежей
    // сессии достаточно, чтобы модель назвала правку отладочной. Надгробие
    // считается наравне с живой целью — оно тоже доказывает, что план в этой
    // сессии одобряли, а закрытая работа права на времянку не отнимает.
    if (goals.some((g) => g.source === 'plan')) return { decision: 'allow' };
    return {
      decision: 'deny',
      reason: 'Заблокировано план-гейтом: правка выглядит черновой, но одобренного плана в реестре нет — чернового без работы не бывает.',
    };
  }
  if (verdict.startsWith('UNCOVERED')) {
    return {
      decision: 'deny',
      reason: `Заблокировано план-гейтом: одобренное этого не покрывает —${verdict.slice('UNCOVERED:'.length)}. Пути дальше: покажи план с этой целью или дай прямое разрешение в диалоге.`,
    };
  }
  return {
    decision: 'deny',
    reason: 'Заблокировано план-гейтом: сверка не дала решения, а без него правка не идёт. Нет решения — блок: ни падение проверки, ни недоступность модели проходом не становятся.',
  };
}
