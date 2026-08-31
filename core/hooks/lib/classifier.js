// Вызов классификатора план-гейта — LLM-проверки, которую регулярка дать не
// может. Сам классификатор остаётся отдельной командой (он оборачивает вызов
// модели), здесь только его запуск и разбор ответа.
//
// Ответ — одна строка. Разбор сверяет СНАЧАЛА отрицательную форму: положительная
// входит в отрицательную подстрокой, и наивная проверка «содержит СООТВЕТСТВУЕТ»
// пропустила бы «НЕ СООТВЕТСТВУЕТ».
//
// Таймаут, обрыв и нераспознанный ответ дают UNAVAILABLE: решение о судьбе
// правки принимает гейт, а не помощник.
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { repoRootOf } from './paths.js';
import { parseCoverVerdict, parsePreflightVerdict } from '../../classifier/verdict.mjs';
import { drainClassifierNotices, formatClassifierNotices, recordClassifierNotice } from '../../classifier/notices.mjs';

// Корень считается от ЭТОГО модуля, а не от файла вызывающего хука: у них разная
// глубина (хук лежит на уровень выше), и общая формула на стороне вызова давала
// бы каталог мимо чекаута — классификатор молча оказывался бы недоступен.
// Срок и число проходов приёма материала. Живут здесь, а не по месту, потому
// что их два и они обязаны быть согласованы: приём тратит бюджет на КАЖДОМ
// проходе, а хук одобрения убивает его снаружи одним числом. Разъехавшись, они
// давали молчаливую пропажу: приём успевал записать часть и погибал.
export const INGEST_BUDGET_SEC = 1500;
export const INGEST_PASSES = 2;

// Потолок хода, за которым приём резать уже не нам: столько отмерено хуку в
// settings.json (timeout у dispatch.js), и на столько же протухает метка
// разбора, которую ждёт сверка. Дедлайн приёма обязан помещаться внутрь — иначе
// хук убьют раньше, чем он успеет записать след, и получится ровно та молчаливая
// пропажа, ради которой след и заводился.
const HOOK_CAP_SEC = 3600;

// Сколько ждать приём снаружи: все его проходы плюс запас на чтение реестра,
// запись и запуск процесса. Меньше — значит резать собственную работу; больше
// потолка хода — значит не резать вовсе, и тогда режет кто-то другой и молча.
export function ingestDeadlineMs() {
  const wanted = INGEST_BUDGET_SEC * INGEST_PASSES + 60;
  return Math.min(wanted, HOOK_CAP_SEC - 60) * 1000;
}

export function classifierPath() {
  return process.env.PLAN_CLASSIFIER_BIN
    || path.join(repoRootOf(import.meta.url), 'core', 'classifier', 'run.sh');
}

export function classifierAvailable(bin) {
  return Boolean(bin) && fs.existsSync(bin);
}

function captureNotices(stderr) {
  let count = 0;
  for (const line of String(stderr || '').split('\n')) {
    if (!line.startsWith('CRAFT_CLASSIFIER_NOTICE ')) continue;
    try {
      if (recordClassifierNotice(JSON.parse(line.slice('CRAFT_CLASSIFIER_NOTICE '.length)))) count += 1;
    } catch { /* malformed backend notice is not user-visible */ }
  }
  return count;
}

export function classifierNoticeText() {
  return formatClassifierNotices(drainClassifierNotices());
}

// classify(bin, mode, args, description) → строка вердикта.
// Аварийный выключатель PLAN_CLASSIFIER=off обрабатывает сам классификатор.
export function classify(bin, mode, args, description, { timeoutSec } = {}) {
  if (!classifierAvailable(bin)) {
    recordClassifierNotice({ type: 'classifier_unavailable', attempts: [] });
    return 'UNAVAILABLE';
  }
  const env = { ...process.env };
  if (timeoutSec) env.PLAN_CLASSIFIER_TIMEOUT = String(timeoutSec);
  const res = spawnSync('bash', [bin, mode, ...args], {
    input: description,
    env,
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  });
  const captured = captureNotices(res.stderr);
  if (res.error || res.signal || res.status !== 0) {
    if (!captured) recordClassifierNotice({ type: 'classifier_unavailable', attempts: [] });
    return 'UNAVAILABLE';
  }
  const verdict = (res.stdout || '').trim();
  if (!verdict) {
    if (!captured) recordClassifierNotice({ type: 'classifier_unavailable', attempts: [] });
    return 'UNAVAILABLE';
  }
  if (mode === 'cover' && !parseCoverVerdict(verdict)) return 'UNAVAILABLE';
  if (mode === 'preflight' && !parsePreflightVerdict(verdict)) return 'UNAVAILABLE';
  return verdict;
}
