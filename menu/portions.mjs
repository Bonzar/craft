#!/usr/bin/env node
// Колонка «нужно» в карточке блюда: сколько чего взять на ближайшую готовку.
// Читает планы, ставит в таблицу состава четвёртый столбец и отдаёт пересчёт
// формулам Craft. Ничего, кроме этого столбца, не трогает.
//
//   node menu/portions.mjs             # проставить по ближайшим готовкам
//   node menu/portions.mjs --dry-run   # показать, что получится

import { createClient } from "./lib/craft-api.mjs";
import { buildModel, indexModel, mealPoint } from "./lib/model.mjs";
import { setPortionsColumn } from "./lib/cards.mjs";

const KINDS = ["cooks", "meals", "purchases", "recipes", "products", "eaters", "measures"];
const WHEN_ORDER = { утро: "завтрак", день: "обед", вечер: "ужин" };

export function parseArgs(argv) {
  const args = { dryRun: false };
  for (const arg of argv) {
    if (arg === "--dry-run") args.dryRun = true;
    else throw new Error(`неизвестный флаг ${arg}`);
  }
  return args;
}

/**
 * Ближайшая запланированная готовка каждого рецепта. Ближайшая, а не сумма:
 * две готовки одного блюда — это два замеса, и в карточке нужен тот, который
 * стоишь делать первым.
 */
export function nextCooks(cooks) {
  const by = new Map();
  for (const cook of cooks) {
    if (cook.status !== "план" || !cook.date || !cook.recipeId) continue;
    const point = mealPoint(cook.date, WHEN_ORDER[cook.when] ?? "завтрак");
    const known = by.get(cook.recipeId);
    if (!known || point < known.point) by.set(cook.recipeId, { cook, point });
  }
  return new Map([...by].map(([id, { cook }]) => [id, cook]));
}

/** Таблица состава — первая за заголовком «Ингредиенты», как её читает model. */
export function ingredientBlock(content = []) {
  const blocks = content ?? [];
  const start = blocks.findIndex((b) => /^#+\s*Ингредиенты/i.test(b.markdown ?? ""));
  return blocks.slice(start === -1 ? 0 : start + 1).find((b) => b.type === "table") ?? null;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const client = createClient({ base: process.env.CRAFT_API_BASE });
  const collections = JSON.parse(process.env.CRAFT_MENU_COLLECTIONS ?? "{}");
  const missing = KINDS.filter((k) => !collections[k]);
  if (missing.length > 0) throw new Error(`не заданы коллекции: ${missing.join(", ")}`);

  const raw = {};
  for (const kind of KINDS) raw[kind] = await client.getItems(collections[kind]);
  const ix = indexModel(buildModel(raw));
  const next = nextCooks(ix.cooks);

  const blocks = [];
  for (const item of raw.recipes) {
    const cook = next.get(item.id);
    if (!cook) continue;
    const table = ingredientBlock(item.content);
    if (!table) continue;
    const name = ix.recipeById.get(item.id)?.name;
    const markdown = setPortionsColumn(table.markdown, cook.portions);
    if (markdown === null) {
      console.log(`${name}: пропущено — в составе нет колонки пересчёта, заведи её сам`);
      continue;
    }
    if (markdown === table.markdown) continue;
    blocks.push({ id: table.id, markdown });
    console.log(`${name}: ${cook.portions} порций — ${cook.name}`);
  }

  if (blocks.length === 0) console.log("нечего проставлять");
  else if (args.dryRun) console.log(`\nкарточек: ${blocks.length}, запись пропущена`);
  else {
    await client.updateBlocks(blocks);
    console.log(`\nкарточек: ${blocks.length}`);
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error(err.message);
    process.exitCode = 2;
  });
}
