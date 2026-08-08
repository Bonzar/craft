// Слой человекоподобного ввода поверх ghost-cursor. Траекторию считает библиотека
// (GC.path — кривые Безье с перелётом и разбросом), здесь только исполнение настоящими
// событиями мыши и клавиатуры и человеческие тайминги.
//
// Тайминги берём из логнормального распределения: у человека паузы редко короткие и с
// длинным хвостом, у скрипта — узкая полка вокруг одного значения.

function _ln(median, sigma) {
  var u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  var z = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  return Math.max(8, Math.round(median * Math.exp(sigma * z)));
}
function _sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

// Позиция курсора живёт на странице, а не в скрипте: скрипты сменяют друг друга, а
// курсор на экране остаётся там, где его оставили.
async function hpos(page) {
  // Во время навигации контекст страницы умирает — это норма, а не отказ: продолжаем
  // с последней разумной позиции, вместо того чтобы ронять весь проход.
  try {
    var p = await page.evaluate(function () { return window.__hcur || null; });
    return p || { x: 300, y: 300 };
  } catch (e) { return { x: 300, y: 300 }; }
}
async function hsetpos(page, x, y) {
  try { await page.evaluate(function (p) { window.__hcur = p; }, { x: x, y: y }); } catch (e) { }
}

// Сколько кадров экрана проходит между соседними событиями мыши. Доли взяты из замера
// живого ввода Влада на этой же машине: 426 движений, 87% идут через один кадр, у
// остального длинный хвост. Ровный шаг в один кадр — метроном, его и опознают.
var _FRAME = 16.67;
var _OVERHEAD = 3.3;  // цена одной отправки события, замерена на этом канале
function _frames() {
  var r = Math.random();
  if (r < 0.014) return 0;   // два события внутри одного кадра
  if (r < 0.880) return 1;
  if (r < 0.929) return 2;
  if (r < 0.941) return 3;
  if (r < 0.948) return 4;
  if (r < 0.950) return 5;
  return 6 + Math.floor(Math.pow(Math.random(), 3) * 55); // редкая долгая задержка
}

// Движение по траектории библиотеки. Точки даёт ghost-cursor, ритм — распределение выше.
async function hmove(page, x, y) {
  var from = await hpos(page);
  var pts = GC.path(from, { x: x, y: y });
  for (var i = 0; i < pts.length; i++) {
    try {
      await page.mouse.move(pts[i].x, pts[i].y);
      // Иногда рука замирает, но кадр всё равно приносит событие с тем же местом:
      // в замере у Влада 5% шагов имеют нулевую длину.
      if (Math.random() < 0.05) {
        await _sleep(_FRAME + (Math.random() - 0.5) * 3);
        await page.mouse.move(pts[i].x, pts[i].y);
      }
    } catch (e) { break; }
    var f = _frames();
    // Отправка события сама стоит времени: без вычета медиана уезжала на 20 мс вместо
    // 16,7, и часть шагов сваливалась в соседний кадр. Замерено на этом канале.
    await _sleep(Math.max(1, f * _FRAME - _OVERHEAD + (Math.random() - 0.5) * 3));
  }
  await hsetpos(page, x, y);
}

// Клик: подвели курсор, замерли, нажали, отпустили. Пауза перед нажатием обязательна —
// человек наводится и чуть медлит, скрипт жмёт мгновенно.
async function hclick(page, x, y) {
  await hmove(page, x, y);
  await _sleep(_ln(120, 0.5));
  await page.mouse.down();
  await _sleep(_ln(70, 0.4));
  await page.mouse.up();
  await _sleep(_ln(320, 0.5));
}

// Зов о помощи: сессия упёрлась и отдаёт свою страницу человеку.
//
// Сетевого вызова в песочнице нет (замерено: fetch там undefined), поэтому зовём
// строкой в вывод — пульт h.sh ловит её, заявляет страницу в пульт капчи и печатает
// готовую ссылку. Работает на ЛЮБОЙ затык, а не только на узнаваемую капчу: проверку
// без адреса и без своего кадра признаками не поймать, а заявкой — можно.
//
// Вместе с адресом отдаём идентификатор своей вкладки: две сессии могут застрять на ОДНОМ
// адресе (общий логин, одна и та же капча), и по адресу пульт выдал бы обеим ссылку на
// первую попавшуюся вкладку — человек прошёл бы проверку не за ту сессию. Идентификатор
// спрашиваем у самой страницы отладочным протоколом: он общий у сессии и у пульта.
async function _цель(page) {
  try {
    var s = await page.context().newCDPSession(page);
    var info = await s.send("Target.getTargetInfo");
    return (info && info.targetInfo && info.targetInfo.targetId) || "-";
  } catch (e) { return "-"; }
}

async function ohelp(page, повод) {
  console.log("ЗАСТРЯЛА " + (await _цель(page)) + " " + page.url() + " :: " + (повод || "нужна помощь"));
}

// Сессия прошла дальше — заявку снимаем, иначе ссылка будет висеть живой.
async function odone(page) {
  console.log("ПРОШЛА " + (await _цель(page)) + " " + page.url());
}

// Ждём человека. Два исхода, и оба видны ИЗ БРАУЗЕРА, потому что сетевого канала до
// пульта у песочницы нет:
//   «решено» — страница ушла с адреса проверки (человек прошёл капчу);
//   «отдано» — человек нажал «Вернуть агенту», и пульт положил метку в саму страницу.
// Ожидание живёт внутри вкладки и до мака не ходит: круги через реле тут не нужны.
async function owait(page, ms) {
  var предел = ms === undefined ? 15 * 60 * 1000 : ms;
  // Условие «адрес больше не проверка» годится ТОЛЬКО если он ею был. Для обычного зова
  // о помощи оно истинно с самого начала, и ожидание закрывается мгновенно — на этом я
  // и обжёгся. Поэтому исходное состояние снимаем до ожидания и передаём внутрь.
  var былаКапча = /xpvnsulc|showcaptcha/i.test(page.url());
  try {
    var исход = await page.waitForFunction(function (капча) {
      if (window.__отдано) return "отдано";
      try { if (localStorage.getItem("__отдано")) return "отдано"; } catch (e) { }
      if (капча && !/xpvnsulc|showcaptcha/i.test(location.href)) return "решено";
      return null;
    }, былаКапча, { timeout: предел, polling: 400 });
    var что = await исход.jsonValue();
    console.log("человек: " + что);
    return что;
  } catch (e) {
    console.log("человек не ответил за отведённое время");
    return "нет";
  }
}

// Окно под сессию, а не вкладка.
//
// Профиль у всех сессий ОДИН — он копит пропуска антиботов и логины, и разводить
// сессии по чистым профилям значит ловить капчу каждой заново. Изоляция идёт окнами:
// у активной вкладки непереднего окна видимость остаётся настоящей, а у фоновой
// вкладки — нет, и это первое, что слушают сайты.
//
// Замерено на двух окнах: обе страницы всё время докладывали «видима, в фокусе», и
// при переключении между окнами не сработало ни одного события blur, focus или
// visibilitychange. Замер шёл при запертом маке и потухшем экране; с живым экраном и
// кликами человека поведение может отличаться — это не проверялось.
async function owindow(anchor, url) {
  var s = await anchor.context().newCDPSession(anchor);
  await s.send("Target.createTarget", { url: url, newWindow: true });
  for (var i = 0; i < 20; i++) {
    await _sleep(400);
    var ps = await browser.listPages();
    for (var j = ps.length - 1; j >= 0; j--) {
      if ((ps[j].url || "").indexOf(url.split("?")[0]) === 0) return browser.getPage(ps[j].id);
    }
  }
  throw new Error("окно не открылось: " + url);
}

// Своё окно вперёд перед работой: часть сайтов смотрит на фокус, и держать его на
// рабочей вкладке дешевле, чем потом гадать, почему поведение отличается.
async function ofocus(page) {
  try { await page.bringToFront(); } catch (e) { }
  await _sleep(_ln(180, 0.4));
}

// Геометрия элемента, устойчиво к двум частым бедам сразу.
//
// Первая: на странице живут ДВА экземпляра одного поля — видимый и скрытый дубль
// (у S7 в DOM две формы). Ссылка по роли или подписи попадает в любой из них, и на
// дубле размеров нет вовсе. Поэтому перебираем все совпадения и берём первое с
// настоящей геометрией, а не слепо верим первому.
//
// Вторая: сразу после загрузки элемент может ещё не разложиться, и размеров нет по
// времени, а не по существу. Поэтому пробуем несколько раз с паузой.
async function _geom(locator) {
  for (var попытка = 0; попытка < 8; попытка++) {
    var n = 1;
    try { n = await locator.count(); } catch (e) { n = 1; }
    for (var i = 0; i < Math.min(n, 6); i++) {
      var cand = n > 1 ? locator.nth(i) : locator;
      var b = null;
      try { b = await cand.boundingBox(); } catch (e) { b = null; }
      if (b && isFinite(b.x) && isFinite(b.y) && b.width > 0 && b.height > 0) return b;
    }
    await _sleep(400);
  }
  return null;
}

// Клик по элементу: подвести в вид, целиться в случайную точку внутри, а не в центр.
async function hclickEl(page, locator) {
  try { await locator.scrollIntoViewIfNeeded({ timeout: 8000 }); } catch (e) { }
  await _sleep(_ln(260, 0.5));
  var b = await _geom(locator);
  if (!b) throw new Error("элемент без геометрии");
  var pad = 0.28;
  var x = b.x + b.width * (pad + Math.random() * (1 - 2 * pad));
  var y = b.y + b.height * (pad + Math.random() * (1 - 2 * pad));
  await hclick(page, Math.round(x), Math.round(y));
}

// Набор: интервалы между клавишами разные, изредка человек задумывается.
async function htype(page, text) {
  for (var i = 0; i < text.length; i++) {
    await page.keyboard.type(text[i]);
    await _sleep(Math.random() < 0.08 ? _ln(380, 0.5) : _ln(105, 0.45));
  }
  await _sleep(_ln(400, 0.4));
}

// Прокрутка трекпадом: разгон, полка, затухание — мелкими тиками раз в кадр.
// В замере живого ввода Влада ряд выглядит так: 1, 5, 10, 19, 21, 21, 13, 9, 6,
// затем новая волна 14, 16, 17, 17, 16, 16, 18. Один кусок в 52 пикселя, как было
// раньше, у человека не встречается вовсе.
async function hscroll(page, dy) {
  var dir = dy > 0 ? 1 : -1;
  var left = Math.abs(dy);
  while (left > 2) {
    // Одна волна: сколько тиков и какой пик
    var peak = Math.min(left, Math.round(14 + Math.random() * 10));
    var n = Math.max(3, Math.round(left / peak * (1.6 + Math.random())));
    n = Math.min(n, 24);
    for (var i = 0; i < n && left > 0; i++) {
      var phase = (i + 0.5) / n;
      // разгон в начале, спад в конце — половина синусоиды с шумом
      var env = Math.sin(Math.PI * Math.pow(phase, 0.75));
      var tick = Math.max(1, Math.round(peak * env * (0.8 + Math.random() * 0.4)));
      tick = Math.min(tick, left);
      try { await page.mouse.wheel(0, dir * tick); } catch (e) { return; }
      left -= tick;
      await _sleep(_FRAME * (Math.random() < 0.85 ? 1 : 2) + (Math.random() - 0.5) * 3);
    }
    if (left > 2) await _sleep(_ln(180, 0.5)); // пауза между волнами
  }
  await _sleep(_ln(420, 0.5));
}

// Ожидание не пустое: пока страница думает, курсор живёт — мелкие сносы и иногда
// подскролл. Мёртвая пауза без единого события — сама по себе признак скрипта.
async function hidle(page, ms) {
  var end = Date.now() + ms;
  while (Date.now() < end) {
    var r = Math.random();
    if (r < 0.55) {
      var p = await hpos(page);
      var dx = (Math.random() - 0.5) * 90;
      var dy = (Math.random() - 0.5) * 60;
      await hmove(page, Math.round(p.x + dx), Math.round(p.y + dy));
    } else if (r < 0.7) {
      try { await hscroll(page, Math.random() < 0.5 ? 90 : -70); } catch (e) { }
    }
    await _sleep(_ln(700, 0.6));
  }
}

// Чтение страницы: человек не действует мгновенно после загрузки.
async function hread(page, ms) {
  await hidle(page, ms === undefined ? _ln(2600, 0.5) : ms);
}

// ---------------------------------------------------------------------------------
// Ввод уровня macOS. Отладочный протокол округляет координаты до целых, живая мышь на
// Retina даёт дробные — разница видна странице. Поэтому жесты отдаём демону, который
// живёт в графической сессии учётки и шлёт настоящие события CGEvent.
// Задание кладём файлом в каталог песочницы, ответ ждём по метке.

var _origin = null;
async function oinit(page) {
  _origin = await page.evaluate(function () {
    return { x: window.screenX, y: window.screenY + (window.outerHeight - window.innerHeight) };
  });
  return _origin;
}
function _scr(x, y) { return [_origin.x + x, _origin.y + y]; }

// Задание кладём СВОИМ файлом и ответ ждём по файлу с тем же номером. На общий файл
// сессии наступают друг другу на руки: вторая затирает задание первой, а обе видят одну
// смену метки и считают, что их ввод прошёл. Номер — случайный, коллизий не ждём.
async function osend(seq) {
  var id = Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
  await writeFile("osin-" + id + ".json", JSON.stringify({ id: id, seq: seq }));
  for (var i = 0; i < 1500; i++) {
    await _sleep(40);
    var now = "";
    try { now = await readFile("osin-" + id + ".done"); } catch (e) { continue; }
    if (!now) continue;
    var res = {};
    try { res = JSON.parse(now); } catch (e) { }
    if (res.err) throw new Error("демон ввода: " + res.err);
    return true;
  }
  throw new Error("демон ввода не ответил");
}

// Точка внутри элемента: целимся не в центр, а в случайное место внутри — как рука.
async function _spot(page, locator) {
  try { await locator.scrollIntoViewIfNeeded({ timeout: 8000 }); } catch (e) { }
  await _sleep(_ln(260, 0.5));
  var b = await _geom(locator);
  if (!b) throw new Error("элемент без геометрии");
  var pad = 0.28;
  return {
    x: b.x + b.width * (pad + Math.random() * (1 - 2 * pad)),
    y: b.y + b.height * (pad + Math.random() * (1 - 2 * pad)),
  };
}

// Путь до точки в экранных координатах: считает ghost-cursor, исполняет система.
async function _path(page, x, y) {
  if (!_origin) await oinit(page);
  if (!isFinite(x) || !isFinite(y)) throw new Error("цель не число: " + x + "," + y);
  var from = await hpos(page);
  var pts = GC.path(from, { x: x, y: y })
    .map(function (p) { return _scr(p.x, p.y); })
    .filter(function (p) { return isFinite(p[0]) && isFinite(p[1]); });
  if (!pts.length) throw new Error("траектория пустая");
  return pts;
}

// --- мышь -------------------------------------------------------------------------
async function omove(page, x, y) {
  await osend([{ op: "move", pts: await _path(page, x, y) }]);
  await hsetpos(page, x, y);
}

async function oclick(page, x, y, opts) {
  var o = opts || {};
  await osend([
    { op: "move", pts: await _path(page, x, y) },
    { op: "click", button: o.button || "left", count: o.count || 1 },
  ]);
  await hsetpos(page, x, y);
}

async function oclickEl(page, locator, opts) {
  var s = await _spot(page, locator);
  await oclick(page, s.x, s.y, opts);
}

async function odblclick(page, x, y) { await oclick(page, x, y, { count: 2 }); }
async function odblclickEl(page, locator) { await oclickEl(page, locator, { count: 2 }); }
async function orightclick(page, x, y) { await oclick(page, x, y, { button: "right" }); }
async function orightclickEl(page, locator) { await oclickEl(page, locator, { button: "right" }); }

// Наведение без нажатия — для меню и подсказок, которые раскрываются по курсору.
async function ohover(page, x, y) {
  await omove(page, x, y);
  await _sleep(_ln(420, 0.4));
}
async function ohoverEl(page, locator) {
  var s = await _spot(page, locator);
  await ohover(page, s.x, s.y);
}

// Протяжка: для ползунков, капчи-поворота, перетаскивания. Кнопка держится всю дорогу.
async function odrag(page, x0, y0, x1, y1) {
  if (!_origin) await oinit(page);
  var pts = GC.path({ x: x0, y: y0 }, { x: x1, y: y1 }).map(function (p) { return _scr(p.x, p.y); });
  pts.unshift(_scr(x0, y0));
  await osend([{ op: "move", pts: await _path(page, x0, y0) }, { op: "drag", pts: pts }]);
  await hsetpos(page, x1, y1);
}
async function odragEl(page, locator, dx, dy) {
  var s = await _spot(page, locator);
  await odrag(page, s.x, s.y, s.x + dx, s.y + dy);
}

// --- клавиатура -------------------------------------------------------------------
async function otype(page, text) { await osend([{ op: "type", text: text }]); }

// Служебная клавиша с модификаторами: okey(page, "enter"), okey(page, "a", ["cmd"]).
async function okey(page, name, mods, count) {
  await osend([{ op: "key", name: name, mods: mods || [], count: count || 1 }]);
}

// Очистить поле по-человечески: встать в него, выделить всё, стереть.
async function oclearEl(page, locator) {
  await oclickEl(page, locator);
  await okey(page, "a", ["cmd"]);
  await okey(page, "backspace");
}

// Заполнить поле целиком: очистить и набрать.
async function ofillEl(page, locator, text) {
  await oclearEl(page, locator);
  await otype(page, text);
}

// Переключить чекбокс или тумблер, если он ещё не в нужном состоянии.
async function otoggleEl(page, locator, want) {
  var now = await locator.isChecked().catch(function () { return null; });
  if (now === want) return "уже " + (want ? "включено" : "выключено");
  await oclickEl(page, locator);
  return "переключено";
}

// Ожидание не пустое и на этом пути: курсор живёт, изредка подкручивается страница.
async function oidle(page, ms) {
  var end = Date.now() + ms;
  while (Date.now() < end) {
    var r = Math.random();
    if (r < 0.5) {
      var p = await hpos(page);
      await omove(page, Math.round(p.x + (Math.random() - 0.5) * 90),
                        Math.round(p.y + (Math.random() - 0.5) * 60));
    } else if (r < 0.62) {
      try { await oscroll(page, Math.random() < 0.5 ? 90 : -70); } catch (e) { }
    }
    await _sleep(_ln(700, 0.6));
  }
}
async function oread(page, ms) { await oidle(page, ms === undefined ? _ln(2600, 0.5) : ms); }
async function oscroll(page, dy) {
  if (!_origin) await oinit(page);
  await osend([{ op: "scroll", dy: dy }]);
}

// Где мы находимся — печатается в конце каждого скрипта, чтобы не терять вкладки.
async function hwhere() {
  var tabs = await browser.listPages();
  console.log("ВКЛАДКИ: " + JSON.stringify(tabs.map(function (t) {
    return { id: t.id.slice(0, 8), url: (t.url || "").slice(0, 70), name: t.name };
  })));
}
