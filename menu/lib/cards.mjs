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

  // Обе стороны равенства обязательны: строка с одной заполненной половиной —
  // это заготовка под ответ, а не ответ. Считать по ней значило бы выдать ноль
  // за перевод и потерять продукт из списка покупок молча.
  const bridge = measures.find(
    (m) => m.productId === product.id && startsWithMeasure(measure, m.measure),
  );
  if (!bridge || !bridge.measureQty || !bridge.productQty) {
    return { qty: null, unit: product.unit, unknown: measure };
  }

  return { qty: (row.qty * bridge.productQty) / bridge.measureQty, unit: product.unit };
}

/**
 * Сводится ли мера к единице продукта — сама единица или запись в «Мерах».
 * Закупка спрашивает про свою единицу, состав — про меру строки.
 */
export function convertible(measure, product, measures) {
  return convert({ measure, qty: 1 }, product, measures).qty !== null;
}

/**
 * Место колонки «нужно»: четвёртая. Первые три заняты — название, базовые
 * порции и пересчёт под большой замес, — а формулам нужен голый номер порций
 * в шапке, поэтому пометить колонку словом нельзя: Craft перестанет видеть
 * в ячейке число и весь столбец посчитается в ошибку.
 */
export const NEED_COLUMN = 3;

/** Буква столбца для формулы: первый — A, второй — B и так далее. */
const columnLetter = (index) => String.fromCharCode(65 + index);

/**
 * Пересчёт строки под колонку «нужно», с округлением до четверти. Четверть —
 * нижний предел доли из правила на странице «Меню»: четверть луковицы и
 * четверть ложки отмерить можно, восьмую — уже нет. На граммах и миллилитрах
 * округление не видно, на штуках и ложках оно и нужно.
 */
const formula = (index, letter) => `=ROUND(B${index}*(${letter}1/B1)*4,0)/4`;

/** Прошлые формы той же формулы — их команда переписывает как свои. */
const generated = (index, letter) => [formula(index, letter), `=B${index}*(${letter}1/B1)`];

/**
 * Колонка «нужно» в таблице состава — под порции ближайшей готовки. Пересчёт
 * делает сам Craft формулой, поэтому цифры в карточке живые: правишь порции
 * готовки, прогоняешь команду — граммовки едут следом.
 *
 * Первый столбец — минимальный замес: меньше этого числа порций блюдо не
 * готовят, рецепт дальше не делится. Поэтому в шапку «нужно» встаёт хотя бы
 * база, даже если готовка запланирована мельче. Заодно это убирает ноль:
 * множитель не бывает меньше единицы, и округление не съедает строку.
 *
 * Формула в ячейке, написанная руками, прогон переживает: округление в свою
 * сторону — решение по блюду, кода оно не касается. Переписываются только те
 * ячейки, что команда ставила сама.
 *
 * Столбцы правее не трогаются и не обрезаются: заведённое руками переживает
 * прогон, даже если стоит за колонкой «нужно».
 *
 * Таблица уже трёх столбцов — не наш случай: колонка встала бы четвёртой, а
 * третья осталась дырой посреди состава. Возвращаем null, и команда скажет об
 * этом вслух, вместо того чтобы молча испортить карточку.
 *
 * Ячейка, где количество словами («по вкусу»), переносится как есть: формула
 * по ней дала бы ошибку, а смысл строки от числа порций не зависит.
 */
export function setPortionsColumn(markdown, portions) {
  const lines = String(markdown ?? "").split("\n");
  const isRow = (l) => l.trim().startsWith("|");
  const isRule = (l) => /^\|[\s|:-]+\|$/.test(l.trim());

  const rows = lines.filter((l) => isRow(l) && !isRule(l));
  if (rows.length === 0) return null;
  if (Math.max(...rows.map((r) => cells(r).length)) < NEED_COLUMN) return null;

  const at = NEED_COLUMN;
  const width = Math.max(...rows.map((r) => cells(r).length), at + 1);
  const letter = columnLetter(at);
  const need = Math.max(portions, number(cells(rows[0])[1]) ?? portions);

  let index = 0;
  return lines
    .map((line) => {
      if (!isRow(line)) return line;
      if (isRule(line)) return `| ${Array.from({ length: width }, () => "---").join(" | ")} |`;
      index += 1;
      const parts = cells(line);
      while (parts.length < width) parts.push("");
      const base = parts[1];
      const own = parts[at] === "" || generated(index, letter).includes(parts[at]);
      parts[at] =
        index === 1 ? String(need)
        : number(base) === null ? base
        : own ? formula(index, letter)
        : parts[at];
      return `| ${parts.join(" | ")} |`;
    })
    .join("\n");
}

/**
 * Вариант ингредиента: сначала тот, что есть дома, иначе первый по порядку.
 * Общая для списка покупок и для списания: разойдись они — покупали бы одно,
 * а тратили другое, и в минус ушёл бы продукт, которого дома нет вовсе.
 */
export function pickProduct(row, productById) {
  const options = row.products.map((p) => productById.get(p.id)).filter(Boolean);
  return options.find((p) => (p.qty ?? 0) > 0) ?? options[0];
}

/** Сколько продукта нужно на заданное число порций. */
export function needFor(row, product, measures, portions, basePortions) {
  const { qty, unit, unknown } = convert(row, product, measures);
  if (qty === null || !basePortions) return { qty: null, unit, unknown };
  return { qty: (qty * portions) / basePortions, unit };
}
