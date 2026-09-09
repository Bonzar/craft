// Состав блюда живёт не в коллекции, а в карточке блюда: таблица сразу за
// заголовком «Ингредиенты». Здесь она разбирается в список продуктов с
// количествами. Правится состав по-прежнему руками в карточке — так Влад его
// и ведёт, а код только читает.

/** Первая ячейка строки «Кол-во порций» может быть жирной: `**Кол-во порций**`. */
const PORTIONS_ROW = /кол-?во\s+порций/i;

/**
 * Ссылка на элемент коллекции «Продукты»: `[Название](block://<id>)`.
 * Идентификатор берётся как есть, без проверки на шестнадцатеричность: иначе
 * смена формата id у Craft молча выбросила бы строку из состава.
 */
const LINK = /\[([^\]]*)\]\(block:\/\/([^)\s]+)\)/g;

/** Варианты «подойдёт любой» пишутся через косую черту или слово «или». */
const ALTERNATIVES = /\s*(?:\/|\bили\b)\s*/;

const cells = (line) =>
  line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

const number = (text) => {
  const m = String(text ?? "").replace(",", ".").match(/-?\d+(?:\.\d+)?/);
  return m ? Number(m[0]) : null;
};

/** Мера из хвоста ячейки: `(ч. л. в соус)` → «ч. л. в соус». */
function measureOf(cell) {
  const tail = cell.replace(LINK, "").trim();
  const m = tail.match(/\(([^()]*)\)\s*$/);
  return m ? m[1].trim() : null;
}

/**
 * Строка состава. Продуктов может быть несколько — это варианты одного
 * ингредиента, и порядок в строке задаёт предпочтение.
 */
function parseRow(line) {
  const [first, second] = cells(line);
  if (first === undefined) return null;

  const products = [...first.matchAll(LINK)].map(([, title, id]) => ({ id, title }));
  const measure = measureOf(first);
  const qty = number(second);

  return {
    products,
    measure,
    qty,
    // «по вкусу», «щепоток» и прочее словами: строка живая, но не считаемая.
    countable: qty !== null && products.length > 0,
    alternatives: products.length > 1,
    raw: line.trim(),
  };
}

/**
 * Разбирает markdown таблицы ингредиентов.
 * Возвращает базовое число порций и строки состава.
 */
export function parseIngredients(markdown) {
  const lines = String(markdown ?? "")
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.startsWith("|") && !/^\|[\s|:-]+\|$/.test(l));
  if (lines.length === 0) return { basePortions: null, rows: [] };

  const head = cells(lines[0]);
  const hasHead = PORTIONS_ROW.test(head[0] ?? "");
  const basePortions = hasHead ? number(head[1]) : null;

  const rows = lines
    .slice(hasHead ? 1 : 0)
    .map(parseRow)
    .filter((r) => r && r.products.length > 0);

  return { basePortions, rows };
}

/**
 * Хвост в скобке — пометка Влада, а не часть меры: «г тёртого», «мл в соус»,
 * «ч. л. в соус». Поэтому мера сравнивается по началу, но обязательно по
 * границе слова: иначе «горстей» сошло бы за «г».
 */
const startsWithMeasure = (measure, candidate) => {
  if (!measure || !candidate || !measure.startsWith(candidate)) return false;
  const next = measure[candidate.length];
  return next === undefined || !/[\p{L}\p{N}]/u.test(next);
};

/**
 * Мера строки сводится к единице продукта через таблицу «Меры».
 * Совпала с единицей продукта — переводить нечего.
 */
export function convert(row, product, measures) {
  const measure = row.measure ?? product.unit;
  if (startsWithMeasure(measure, product.unit)) return { qty: row.qty, unit: product.unit };

  const bridge = measures.find(
    (m) => m.productId === product.id && startsWithMeasure(measure, m.measure),
  );
  if (!bridge || !bridge.measureQty) return { qty: null, unit: product.unit, unknown: measure };

  return { qty: (row.qty * bridge.productQty) / bridge.measureQty, unit: product.unit };
}

/**
 * Сводится ли мера к единице продукта — сама единица или запись в «Мерах».
 * Закупка спрашивает про свою единицу, состав — про меру строки.
 */
export function convertible(measure, product, measures) {
  return convert({ measure, qty: 1 }, product, measures).qty !== null;
}

/** Сколько продукта нужно на заданное число порций. */
export function needFor(row, product, measures, portions, basePortions) {
  const { qty, unit, unknown } = convert(row, product, measures);
  if (qty === null || !basePortions) return { qty: null, unit, unknown };
  return { qty: (qty * portions) / basePortions, unit };
}
