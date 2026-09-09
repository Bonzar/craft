#!/usr/bin/env node
// Пересчёт недели: читает коллекции Craft, прогоняет правила, пишет находки
// в поле problem. Что и в какой день — решает человек, код только проверяет.
//
//   node menu/recheck.mjs --cooks <id> --meals <id> --purchases <id> \
//                        --recipes <id> --products <id> --eaters <id> --measures <id>
//   node menu/recheck.mjs --discover            # показать коллекции и их id
//   node menu/recheck.mjs ... --from 2026-09-08/обед   # не трогать прошлое
//   node menu/recheck.mjs ... --dry-run         # только отчёт, без записи

import { createClient } from "./lib/craft-api.mjs";
import { SLOTS, buildModel, mealPoint, slotIndex } from "./lib/model.mjs";
import { runRules } from "./lib/rules.mjs";

const KINDS = ["cooks", "meals", "purchases", "recipes", "products", "eaters", "measures"];

export function parseArgs(argv) {
  const args = { collections: {}, from: null, dryRun: false, discover: false, quiet: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--dry-run") args.dryRun = true;
    else if (arg === "--discover") args.discover = true;
    else if (arg === "--quiet") args.quiet = true;
    else if (arg === "--from") args.from = argv[++i];
    else if (arg.startsWith("--")) {
      const kind = arg.slice(2);
      if (!KINDS.includes(kind)) throw new Error(`неизвестный флаг ${arg}`);
      args.collections[kind] = argv[++i];
    } else throw new Error(`лишний аргумент ${arg}`);
  }
  return args;
}

/** «2026-09-08/обед» -> точка отсчёта; без слота — с начала дня. */
export function parseFrom(from) {
  if (!from) return null;
  const [date, slot] = from.split("/");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error(`--from: нужна дата вида 2026-09-08`);
  // Опечатка в слоте дала бы точку «дата#-1», через которую проходит весь день:
  // защита от правки прошлого молча перестала бы работать.
  if (slot !== undefined && slotIndex(slot) === -1) {
    throw new Error(`--from: неизвестный слот «${slot}», нужен один из: ${SLOTS.join(", ")}`);
  }
  return slot ? mealPoint(date, slot) : mealPoint(date, "завтрак");
}

/**
 * Горизонт записи — самая поздняя точка, до которой она дотягивается.
 * Приём отвечает сам за себя, поэтому его тут нет. Готовка живёт, пока её едят:
 * пюре сварено в понедельник, а не хватит его в четверг. Закупка живёт, пока
 * жива готовка, под которую взята.
 */
export function horizons(model) {
  const cooks = new Map(
    model.cooks.map((c) => [c.id, c.date ? mealPoint(c.date, "завтрак") : null]),
  );
  for (const meal of model.meals) {
    if (!meal.date) continue;
    const point = mealPoint(meal.date, meal.slot);
    for (const id of [...meal.hot, ...meal.side, ...meal.extra]) {
      if (!cooks.has(id)) continue;
      const known = cooks.get(id);
      if (known === null || known < point) cooks.set(id, point);
    }
  }

  const purchases = new Map(
    model.purchases.map((p) => {
      const own = p.date ? mealPoint(p.date, "завтрак") : null;
      const reach = p.forIds.map((id) => cooks.get(id)).filter(Boolean);
      return [p.id, [own, ...reach].filter(Boolean).sort().pop() ?? null];
    }),
  );

  return { cooks, purchases };
}

/**
 * Записи раньше точки пересчёта не трогаем: прошлое уже случилось. Прошлым
 * запись становится, только когда прошло всё, чего она касается, — и то, что
 * всё ещё в плане, не прошло вовсе: дозакупка на среду стоит датой понедельника.
 */
export function isAfter(record, kind, since, until = null) {
  if (!since) return true;
  if (record.status === "план") return true;
  if (kind === "meals") return mealPoint(record.date, record.slot) >= since;
  if (!record.date) return true;
  return (until ?? mealPoint(record.date, "завтрак")) >= since;
}

function envCollections() {
  const raw = process.env.CRAFT_MENU_COLLECTIONS;
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error("CRAFT_MENU_COLLECTIONS: не JSON");
  }
}

export async function recheck({ client, collections, since, dryRun }) {
  const raw = {};
  for (const kind of KINDS) raw[kind] = await client.getItems(collections[kind]);
  const model = buildModel(raw);
  const found = runRules(model);
  const reach = horizons(model);

  const report = [];
  const writes = new Map();
  for (const kind of ["cooks", "meals", "purchases"]) {
    const updates = [];
    for (const record of model[kind]) {
      if (!isAfter(record, kind, since, reach[kind]?.get(record.id) ?? null)) continue;
      const problem = found.get(`${kind}:${record.id}`) ?? "";
      updates.push({ id: record.id, properties: { problem } });
      if (problem) report.push({ kind, name: record.name, problem });
    }
    writes.set(kind, updates);
  }

  if (!dryRun) {
    for (const [kind, updates] of writes) await client.updateItems(collections[kind], updates);
  }
  return { report, touched: [...writes].map(([kind, u]) => [kind, u.length]) };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const client = createClient({ base: process.env.CRAFT_API_BASE });

  if (args.discover) {
    for (const c of await client.listCollections()) {
      console.log(`${c.id}  ${String(c.itemCount).padStart(4)}  ${c.name}`);
    }
    return;
  }

  const collections = { ...envCollections(), ...args.collections };
  const missing = KINDS.filter((k) => !collections[k]);
  if (missing.length > 0) {
    throw new Error(
      `не заданы коллекции: ${missing.join(", ")}. Дай их флагами или через CRAFT_MENU_COLLECTIONS; список id — --discover`,
    );
  }

  const { report, touched } = await recheck({
    client,
    collections,
    since: parseFrom(args.from),
    dryRun: args.dryRun,
  });

  if (!args.quiet) {
    const order = { cooks: 0, meals: 1, purchases: 2 };
    report.sort((a, b) => order[a.kind] - order[b.kind] || a.name.localeCompare(b.name, "ru"));
    for (const row of report) console.log(`[${row.kind}] ${row.name} — ${row.problem}`);
    const counts = touched.map(([kind, n]) => `${kind} ${n}`).join(", ");
    console.log(
      report.length === 0
        ? `\nвсё сходится (проверено: ${counts})`
        : `\nнаходок: ${report.length} (проверено: ${counts})${args.dryRun ? ", запись пропущена" : ""}`,
    );
  }
  process.exitCode = report.length === 0 ? 0 : 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error(err.message);
    process.exitCode = 2;
  });
}
