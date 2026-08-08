// Проверка «1в1»: свой перенос из gc.js и библиотека ghost-cursor при ОДНОМ И ТОМ ЖЕ потоке
// Math.random обязаны выдавать побитово одинаковый результат.
//
// Как это возможно: обе реализации тянут случайные числа в одном порядке и считают ту же
// арифметику в том же порядке. Поэтому подменяем Math.random детерминированным генератором,
// перед каждым прогоном ставим одинаковое зерно и сравниваем выходы.
//
// Прогон разовый, под ревизию: библиотеки в репозитории нет, путь к её node_modules
// передаётся аргументом. Сходится на ghost-cursor 1.4.2.
//
//   npm i --no-save ghost-cursor@1.4.2 && node tools/mac/gc-equiv.js ./node_modules
const path = require("path");
const GCLIB_DIR = process.argv[2];
if (!GCLIB_DIR) {
  console.error("нужен путь до node_modules с ghost-cursor 1.4.2 первым аргументом");
  process.exit(2);
}
const spoof = require(path.join(GCLIB_DIR, "ghost-cursor/lib/spoof"));
const math = require(path.join(GCLIB_DIR, "ghost-cursor/lib/math"));

// Генератор с зерном: mulberry32. Своей случайности не добавляет — только повторяемость.
function seeded(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const src = require("fs").readFileSync(path.join(__dirname, "gc.js"), "utf8");
const GC = new Function(src + "; return GC;")();

const реальный = Math.random;
function прогон(seed, fn) {
  Math.random = seeded(seed);
  try { return fn(); } finally { Math.random = реальный; }
}

function точки(n) {
  const r = seeded(999 + n);
  return {
    start: { x: r() * 1600, y: r() * 900 },
    end: { x: r() * 1600, y: r() * 900, width: n % 3 === 0 ? 0 : 20 + r() * 300, height: 30 },
  };
}

let всего = 0, расхождений = 0, первое = null;
for (let n = 0; n < 400; n++) {
  const { start, end } = точки(n);
  const опции = n % 4 === 0 ? undefined : (n % 4 === 1 ? 40 : { spreadOverride: 10 + (n % 7) });

  const свой = прогон(n + 1, () => GC.path(start, end, опции));
  const библ = прогон(n + 1, () => spoof.path(start, end, опции));
  всего++;
  const a = JSON.stringify(свой.map((p) => [p.x, p.y]));
  const b = JSON.stringify(библ.map((p) => [p.x, p.y]));
  if (a !== b) { расхождений++; if (!первое) первое = { n, длины: [свой.length, библ.length], свой: свой.slice(0, 2), библ: библ.slice(0, 2) }; }

  // overshoot и два случайных помощника — тем же порядком
  const o1 = прогон(n + 7, () => math.overshoot({ x: start.x, y: start.y }, 100));
  const o2 = прогон(n + 7, () => GC.overshoot({ x: start.x, y: start.y }, 100));
  const r1 = прогон(n + 11, () => math.randomNumberRange(3, 91));
  const r2 = прогон(n + 11, () => GC.randomNumberRange(3, 91));
  const v1 = прогон(n + 13, () => math.randomVectorOnLine(start, end));
  const v2 = прогон(n + 13, () => GC.randomVectorOnLine(start, end));
  всего += 3;
  if (JSON.stringify(o1) !== JSON.stringify(o2)) { расхождений++; первое = первое || { n, что: "overshoot", o1, o2 }; }
  if (r1 !== r2) { расхождений++; первое = первое || { n, что: "randomNumberRange", r1, r2 }; }
  if (JSON.stringify(v1) !== JSON.stringify(v2)) { расхождений++; первое = первое || { n, что: "randomVectorOnLine", v1, v2 }; }
}

console.log("сравнений: " + всего + ", расхождений: " + расхождений);
if (первое) console.log("первое расхождение: " + JSON.stringify(первое, null, 1));
process.exit(расхождений ? 1 : 0);
