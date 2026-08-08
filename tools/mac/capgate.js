// Пульт капчи: одна ссылка — одна проверка, живёт до тех пор, пока проверка не пройдена.
//
// Запуск на маке:  node capgate.js <порт-отладки-chrome> <порт-слушателя>
// Наружу отдаётся через tailscale serve, он же держит TLS, поэтому слушаем localhost по http.
// Внешний адрес фронта задаётся переменной CAPGATE_BASE из /Users/agent/.capgate.env.
//
// УСТРОЙСТВО. Профиль у всех сессий один, браузер один — значит проверки всех сессий
// это просто вкладки рядом. Пульт узнаёт вкладку с проверкой и выпускает на неё разовый
// ключ. Ссылка с ключом показывает ТОЛЬКО эту вкладку: чужие проверки и рабочие вкладки
// с неё недоступны вовсе. Общего пульта управления браузером здесь нет намеренно —
// корень отдаёт только счётчик. Утёкшая ссылка даёт максимум одну чужую проверку, и то
// до её решения.
//
// ЧЕМ УЗНАЁМ ПРОВЕРКУ, три слоя:
//  1. адрес верхнего уровня — капчи, ради которых сайт уводит на отдельную страницу;
//  2. вложенные кадры — капча-компонент адрес не меняет, но живёт в кадре со своего
//     домена (reCAPTCHA, hCaptcha, Turnstile, SmartCaptcha и прочие);
//  3. заявка от сессии — ловит ЛЮБУЮ проверку, включая самодельную без единой приметы.
//     Признаки это удобство, заявка это опора.
//
// Ключ гаснет ровно тогда, когда проверка пройдена. До этого ссылку можно открывать
// сколько угодно. После — «пройдено», и через минуту ключ забывается совсем.
//
// ОТКЛИК. Канал до мака идёт через реле, поэтому решает не частота опроса, а размер
// кадра и число кругов на действие: кадры берём трансляцией Chrome (снимок с
// уменьшением менял масштаб страницы, и экран мака заметно «дышал»), запрос кадра
// висит до изменения картинки, точки протяжки уходят пачкой в одном запросе.
const http = require("http");
const fs = require("fs");
const crypto = require("crypto");
const path = "/Users/agent/.npm-global/lib/node_modules/dev-browser/node_modules/playwright-core";
const { chromium } = require(path);

const CDP_PORT = process.argv[2] || "9333";
const LISTEN_PORT = Number(process.argv[3] || 8899);
const HOST = process.env.CAPGATE_HOST || "127.0.0.1";
const QUALITY = Number(process.env.CAPGATE_QUALITY || 45);
// Адрес пульта — только из окружения: имя узла это персональные данные Влада, в
// репозитории их нет. Значение кладётся в /Users/agent/.capgate.env на самом маке.
const BASE = process.env.CAPGATE_BASE;
if (!BASE) {
  console.error("нет CAPGATE_BASE — положи строку «export CAPGATE_BASE=https://<узел>:<порт>» в /Users/agent/.capgate.env");
  process.exit(2);
}
const CAPTCHA = /xpvnsulc|showcaptcha|\/captcha/i;
const CAPTCHA_FRAME = /recaptcha|hcaptcha|turnstile|smartcaptcha|captcha|challenges\.cloudflare|geetest|funcaptcha|arkoselabs|xpvnsulc/i;
const FORGET_MS = 60000;

const CF_TEAM = process.env.CAPGATE_CF_TEAM || "";
const CF_AUD = process.env.CAPGATE_CF_AUD || "";

let jwks = { t: 0, keys: {} };
async function keysCF() {
  const now = Date.now();
  if (now - jwks.t < 3600000 && Object.keys(jwks.keys).length) return jwks.keys;
  const r = await fetch("https://" + CF_TEAM + ".cloudflareaccess.com/cdn-cgi/access/certs");
  const j = await r.json();
  const keys = {};
  for (const k of j.keys || []) keys[k.kid] = crypto.createPublicKey({ key: k, format: "jwk" });
  if (Object.keys(keys).length) jwks = { t: now, keys };
  return jwks.keys;
}

// Проверка удостоверения от Cloudflare Access. Нужна, хотя вход и так за логином:
// Access стоит ПЕРЕД пультом, и кто угодно, кто нашёл источник мимо прокси, попадал бы
// внутрь без логина. Источник обязан проверять подпись сам. Пока переменные пусты,
// пульт считает, что его закрывает сеть Tailscale.
async function checkCF(req) {
  if (!CF_TEAM || !CF_AUD) return true;
  const cookie = /CF_Authorization=([^;]+)/.exec(req.headers.cookie || "");
  const token = req.headers["cf-access-jwt-assertion"] || (cookie && cookie[1]);
  if (!token) return false;
  const [h, p, s] = String(token).split(".");
  if (!h || !p || !s) return false;
  const head = JSON.parse(Buffer.from(h, "base64url").toString());
  const body = JSON.parse(Buffer.from(p, "base64url").toString());
  const key = (await keysCF())[head.kid];
  if (!key) return false;
  if (!crypto.verify("RSA-SHA256", Buffer.from(h + "." + p), key, Buffer.from(s, "base64url"))) return false;
  if (body.exp && body.exp * 1000 < Date.now()) return false;
  const aud = Array.isArray(body.aud) ? body.aud : [body.aud];
  if (!aud.includes(CF_AUD)) return false;
  return body.iss === "https://" + CF_TEAM + ".cloudflareaccess.com";
}

const PAGE_HTML = `<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Проверка</title>
<style>
 html,body{margin:0;height:100%;background:#111;color:#ddd;font:14px -apple-system,system-ui,sans-serif;
   overscroll-behavior:none;touch-action:none;overflow:hidden;
   -webkit-user-select:none;user-select:none;-webkit-touch-callout:none}
 #bar{position:fixed;top:0;left:0;right:0;padding:6px 8px;background:#1b1b1b;z-index:5;
   display:flex;gap:6px;align-items:center;font-size:12px;white-space:nowrap;overflow-x:auto}
 #bar b{flex:0 0 auto} #info{flex:0 0 auto;color:#999}
 button{background:#2a2a2a;color:#ddd;border:1px solid #444;border-radius:5px;padding:5px 9px;font-size:12px;flex:0 0 auto}
 #hand{margin-left:auto;background:#3a2020;border-color:#6a3030}
 #wrap{position:absolute;top:34px;bottom:0;left:0;right:0;overflow:hidden;display:flex;
   align-items:flex-start;justify-content:center}
 #stage{position:relative;display:inline-block;transform-origin:0 0;will-change:transform}
 img{max-width:100%;max-height:100%;display:block;pointer-events:none;-webkit-user-drag:none}
 #pad{position:absolute;left:0;top:0;right:0;bottom:0;z-index:2;touch-action:none;background:transparent}
 #typer{position:fixed;left:-9999px;top:0;width:10px;opacity:0}
 #done{position:absolute;inset:0;display:none;align-items:center;justify-content:center;
   flex-direction:column;gap:8px;background:#111;z-index:6;text-align:center;padding:20px}
 #done b{font-size:20px;color:#7ddf9a}
</style>
<div id="bar">
  <b id="st">соединяюсь…</b><span id="info"></span>
  <button id="up">▲</button><button id="dn">▼</button>
  <button id="kb">Клавиатура</button><button id="zr">1×</button>
  <button id="hand">Вернуть агенту</button>
</div>
<div id="wrap"><div id="stage"><img id="v" alt=""><div id="pad"></div></div></div>
<input id="typer" autocapitalize="off" autocorrect="off" autocomplete="off" spellcheck="false">
<div id="done"><b id="dt">Проверка пройдена</b><span id="ds">Ссылка погасла — она была одноразовой.</span>
  <button id="back" style="display:none">Взять управление обратно</button></div>
<script>
const K = location.pathname.split("/")[2];
const v = document.getElementById("v"), pad = document.getElementById("pad");
const stage = document.getElementById("stage");
const st = document.getElementById("st"), info = document.getElementById("info");
const done = document.getElementById("done"), dt = document.getElementById("dt"), ds = document.getElementById("ds");
const back = document.getElementById("back"), typer = document.getElementById("typer");
let seq = 0, finished = false, paused = false;

// Масштаб ТОЛЬКО у нас на экране: страницу на маке не трогаем вовсе, иначе сайт увидит
// чужую вёрстку и чужой размер окна. Двигаем и увеличиваем саму картинку кадра.
let zoom = 1, panX = 0, panY = 0;
function applyZoom() {
  stage.style.transform = "translate(" + panX + "px," + panY + "px) scale(" + zoom + ")";
  document.getElementById("zr").textContent = (zoom < 1.05 ? "1" : zoom.toFixed(1)) + "×";
}

function finish(заголовок, под, можноВернуть) {
  finished = true;
  dt.textContent = заголовок || "Проверка пройдена";
  ds.textContent = под || "Ссылка погасла — она была одноразовой.";
  back.style.display = можноВернуть ? "inline-block" : "none";
  done.style.display = "flex";
  st.textContent = можноВернуть ? "на паузе" : "готово";
}

async function loop() {
  while (!finished) {
    const t0 = Date.now();
    try {
      const r = await fetch("/c/" + K + "/frame?since=" + seq);
      if (r.status === 410) return finish();
      if (r.status === 409) { return finish("Управление у агента", "Трансляция остановлена, ресурсы не тратятся.", true); }
      if (r.status === 204) continue;
      if (!r.ok) throw new Error(r.status);
      seq = Number(r.headers.get("x-seq") || seq);
      const b = await r.blob();
      const old = v.src;
      v.src = URL.createObjectURL(b);
      if (old.startsWith("blob:")) URL.revokeObjectURL(old);
      st.textContent = (Date.now() - t0) + " мс";
    } catch (e) {
      st.textContent = "нет кадра";
      await new Promise(function (r) { setTimeout(r, 700); });
    }
  }
}
loop();

async function state() {
  if (finished) return;
  try {
    const r = await fetch("/c/" + K + "/state");
    if (r.status === 410) return finish();
    const s = await r.json();
    if (s.пройдена) return finish();
    if (s.пауза) return finish("Управление у агента", "Трансляция остановлена, ресурсы не тратятся.", true);
    info.textContent = (s.повод ? s.повод + " · " : "") + "ждёт " + s.ждёт + " с";
  } catch (e) {}
}
setInterval(state, 3000); state();

// Ввод копим и шлём пачкой: один круг до мака вместо десяти, порядок держит массив.
let buf = [], flying = false;
async function flush() {
  if (flying || !buf.length || finished) return;
  flying = true;
  const batch = buf; buf = [];
  try {
    const r = await fetch("/c/" + K + "/input", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ шаги: batch }),
    });
    if (r.status === 410) finish();
  } catch (e) {}
  flying = false;
  if (buf.length) flush();
}
function push(type, p) { buf.push({ type: type, x: p.x, y: p.y }); flush(); }

// Доля от картинки, а не пиксели: пересчёт в координаты страницы делает сервер по её
// настоящему вьюпорту. Наш масштаб и сдвиг тут не мешают — доля берётся от самой
// картинки, и увеличение на телефоне на страницу не влияет.
function pt(ev) {
  const r = v.getBoundingClientRect();
  const t = (ev.touches && ev.touches[0]) || (ev.changedTouches && ev.changedTouches[0]) || ev;
  return {
    x: Math.min(1, Math.max(0, (t.clientX - r.left) / r.width)),
    y: Math.min(1, Math.max(0, (t.clientY - r.top) / r.height)),
  };
}

// Разделение жестов без переключателей режимов:
//   один палец → ввод в страницу (клик, протяжка слайдера);
//   два пальца → наш просмотр: щипок увеличивает, движение двигает картинку;
//   кнопки ▲ ▼ → прокрутка самой страницы колесом.
// Иначе щипок и протяжка спорили бы за один и тот же жест.
const stopEv = function (e) { e.preventDefault(); e.stopPropagation(); };
let двумя = false, база = 0, zBase = 1, pBase = null, зажато = false, последняя = null;
const дист = function (t) { return Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY); };
const центр = function (t) { return { x: (t[0].clientX + t[1].clientX) / 2, y: (t[0].clientY + t[1].clientY) / 2 }; };

pad.addEventListener("touchstart", function (e) {
  stopEv(e);
  if (e.touches.length >= 2) {
    // Первый палец уже поставил странице «down». Уходя в щипок, обязаны его отпустить,
    // иначе страница остаётся с зажатой кнопкой и следующее одиночное касание тянет
    // элемент вместо нажатия.
    if (зажато && последняя) { push("up", последняя); зажато = false; }
    двумя = true; база = дист(e.touches); zBase = zoom;
    pBase = центр(e.touches); pBase.px = panX; pBase.py = panY;
    return;
  }
  if (!двумя) { последняя = pt(e); зажато = true; push("down", последняя); }
}, { passive: false });

pad.addEventListener("touchmove", function (e) {
  stopEv(e);
  if (e.touches.length >= 2 && двумя) {
    const d = дист(e.touches);
    if (база > 0) zoom = Math.min(6, Math.max(1, zBase * (d / база)));
    const c = центр(e.touches);
    panX = pBase.px + (c.x - pBase.x);
    panY = pBase.py + (c.y - pBase.y);
    applyZoom();
    return;
  }
  if (!двумя) { последняя = pt(e); push("move", последняя); }
}, { passive: false });

function отпустить(e) {
  stopEv(e);
  if (двумя) { if (!e.touches || e.touches.length === 0) двумя = false; return; }
  зажато = false;
  push("up", pt(e));
}
pad.addEventListener("touchend", отпустить, { passive: false });
pad.addEventListener("touchcancel", отпустить, { passive: false });
pad.addEventListener("contextmenu", stopEv);
pad.addEventListener("mousedown", function (e) { stopEv(e); push("down", pt(e)); });
pad.addEventListener("mousemove", function (e) { if (e.buttons) { stopEv(e); push("move", pt(e)); } });
pad.addEventListener("mouseup", function (e) { stopEv(e); push("up", pt(e)); });

function крутить(dy) {
  fetch("/c/" + K + "/scroll", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ dy: dy }) });
}
document.getElementById("up").onclick = function () { крутить(-320); };
document.getElementById("dn").onclick = function () { крутить(320); };
document.getElementById("zr").onclick = function () { zoom = 1; panX = 0; panY = 0; applyZoom(); };

// Клавиатура телефона: прячем настоящее поле и шлём каждый набранный символ в страницу.
// Так работают и раскладка, и автодополнение — набирает всё равно человек.
// Набранное копим и шлём одной посылкой, с одним запросом в полёте. Посылать каждый
// символ отдельным запросом через реле — верный способ забить канал и получить
// «зависшую» страницу: они выстраиваются в очередь и приходят с растущим отставанием.
document.getElementById("kb").onclick = function () { typer.focus(); };
let kbuf = "", kflying = false, ktimer = null;
async function kflush() {
  if (kflying || (!kbuf.length && !kkeys.length)) return;
  kflying = true;
  const текст = kbuf; kbuf = "";
  const клавиши = kkeys; kkeys = [];
  try {
    if (текст) {
      await fetch("/c/" + K + "/key", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ текст: текст }) });
    }
    for (const кл of клавиши) {
      await fetch("/c/" + K + "/key", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ клавиша: кл }) });
    }
  } catch (e) {}
  kflying = false;
  if (kbuf.length || kkeys.length) kflush();
}
let kkeys = [];
typer.addEventListener("input", function () {
  kbuf += typer.value; typer.value = "";
  clearTimeout(ktimer);
  ktimer = setTimeout(kflush, 120);   // склеиваем быстрый набор в одну посылку
});
typer.addEventListener("keydown", function (e) {
  if (e.key === "Enter" || e.key === "Backspace" || e.key === "Tab") {
    e.preventDefault();
    clearTimeout(ktimer);
    kkeys.push(e.key);
    kflush();
  }
});

// Возврат управления агенту: гасим трансляцию, чтобы не тратить канал и мак впустую.
document.getElementById("hand").onclick = async function () {
  await fetch("/c/" + K + "/stop", { method: "POST" });
  finish("Управление у агента", "Трансляция остановлена, ресурсы не тратятся.", true);
};
back.onclick = async function () {
  await fetch("/c/" + K + "/resume", { method: "POST" });
  finished = false; seq = 0; done.style.display = "none"; st.textContent = "…";
  loop();
};
applyZoom();
</script>`;

const DONE_HTML = `<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проверка пройдена</title>
<style>html,body{margin:0;height:100%;display:flex;align-items:center;justify-content:center;
 background:#111;color:#ddd;font:16px -apple-system,system-ui,sans-serif;text-align:center;padding:20px}
 b{color:#7ddf9a;font-size:20px;display:block;margin-bottom:8px}</style>
<div><b>Проверка пройдена</b>Ссылка погасла — она была одноразовой.</div>`;

(async () => {
  const browser = await chromium.connectOverCDP("http://127.0.0.1:" + CDP_PORT);
  browser.on("disconnected", () => { console.error("браузер отвалился, выхожу"); process.exit(1); });

  const pages = () => browser.contexts().flatMap((c) => c.pages()).filter((p) => !p.isClosed());

  const keys = new Map();      // ключ → {page, born, solved, paused, повод}
  const byPage = new Map();

  const mint = (page, повод) => {
    if (byPage.has(page)) {
      const k = byPage.get(page);
      const rec = keys.get(k);
      // Ключ уже погашен, а вкладка упёрлась во вторую проверку подряд: вернуть старый
      // ключ значит отдать человеку мёртвую ссылку. Выпускаем новый, прежний остаётся
      // погасшим — свойство «одна ссылка = одна проверка» держится в обе стороны.
      if (rec && !rec.solved) {
        if (повод) rec.повод = повод;
        return k;
      }
      keys.delete(k);
      byPage.delete(page);
    }
    const k = crypto.randomBytes(16).toString("base64url");
    keys.set(k, { page, born: Date.now(), solved: 0, paused: false, повод: повод || "" });
    byPage.set(page, k);
    console.log("новая проверка, ключ выпущен: " + BASE + "/c/" + k + (повод ? "  повод: " + повод : ""));
    return k;
  };

  const sessions = new WeakMap();
  const cdp = async (page) => {
    let s = sessions.get(page);
    if (!s) { s = await page.context().newCDPSession(page); sessions.set(page, s); }
    return s;
  };

  const заявленные = new Set();

  // Адреса вложенных кадров. Список Playwright показывает только те переходы, которые он
  // наблюдал сам: кадр, появившийся ДО подключения пульта, приходит с пустым адресом —
  // замерено, именно на этом первая версия детектора промахнулась молча. Поэтому дерево
  // кадров спрашиваем у браузера напрямую, с коротким кэшем.
  const treeCache = new WeakMap();
  const адресаКадров = async (page) => {
    const now = Date.now();
    const c = treeCache.get(page);
    if (c && now - c.t < 3000) return c.v;
    let urls = [];
    try { urls = page.frames().map((f) => f.url() || ""); } catch (e) { }
    if (urls.some((u) => !u)) {
      try {
        const s = await cdp(page);
        const t = await s.send("Page.getFrameTree");
        const обойти = (node) => {
          urls.push(node.frame && node.frame.url ? node.frame.url : "");
          (node.childFrames || []).forEach(обойти);
        };
        обойти(t.frameTree);
      } catch (e) { }
    }
    treeCache.set(page, { t: now, v: urls });
    return urls;
  };

  const хватает = async (page) => {
    if (заявленные.has(page)) return true;
    if (CAPTCHA.test(page.url())) return true;
    const urls = await адресаКадров(page);
    return urls.some((u) => CAPTCHA_FRAME.test(u));
  };

  const scan = async () => {
    for (const p of pages()) if (await хватает(p)) mint(p);
    const now = Date.now();
    for (const [k, rec] of keys) {
      const закрыта = rec.page.isClosed();
      if (!закрыта && await хватает(rec.page)) continue;
      if (!rec.solved) {
        rec.solved = now;
        // Две РАЗНЫЕ причины гашения, и путать их нельзя: пройденная проверка — успех,
        // закрытая вкладка — обычно умершая сессия, то есть чей-то сбой. Одна фраза на
        // оба случая уже стоила мне часа поисков вслепую.
        (rec.ждущие || []).forEach((fn) => fn(закрыта ? "вкладка закрыта" : "решено")); rec.ждущие = [];
        console.log((закрыта ? "вкладка закрыта, ключ гаснет: " : "проверка пройдена, ключ гаснет: ") + k);
      }
      if (now - rec.solved > FORGET_MS) { keys.delete(k); byPage.delete(rec.page); }
    }
  };
  setInterval(() => { scan().catch(() => { }); }, 1500);
  await scan();

  // Кадры берём трансляцией, а не снимками: снимок с уменьшением временно меняет
  // масштаб страницы, и при нескольких кадрах в секунду экран мака заметно «дышит».
  const casts = new WeakMap();
  const startCast = async (page) => {
    let c = casts.get(page);
    if (c && c.on) return c;
    if (!c) { c = { seq: 0, buf: null, waiting: [], on: false }; casts.set(page, c); }
    const s = await cdp(page);
    if (!c.hooked) {
      c.hooked = true;
      s.on("Page.screencastFrame", (f) => {
        c.seq++;
        c.buf = Buffer.from(f.data, "base64");
        const w = c.waiting; c.waiting = [];
        w.forEach((fn) => fn());
        s.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => { });
      });
    }
    await s.send("Page.startScreencast", {
      format: "jpeg", quality: QUALITY, maxWidth: 1440, maxHeight: 900, everyNthFrame: 1,
    }).catch(() => { });
    c.on = true;
    return c;
  };
  const stopCast = async (page) => {
    const c = casts.get(page);
    if (!c || !c.on) return;
    try { const s = await cdp(page); await s.send("Page.stopScreencast"); } catch (e) { }
    c.on = false;
    const w = c.waiting; c.waiting = [];
    w.forEach((fn) => fn());
  };

  let vpCache = { t: 0, v: { w: 1440, h: 900 } };
  const viewport = async (page) => {
    const now = Date.now();
    if (now - vpCache.t < 5000) return vpCache.v;
    try {
      const v = await page.evaluate(() => ({ w: window.innerWidth, h: window.innerHeight }));
      if (v && v.w) vpCache = { t: now, v };
    } catch (e) { }
    return vpCache.v;
  };

  let raised = 0;
  const raise = async (page, force) => {
    const now = Date.now();
    if (!force && now - raised < 4000) return;
    raised = now;
    await page.bringToFront().catch(() => { });
  };

  const body = (req) => new Promise((res) => {
    let d = ""; req.on("data", (c) => (d += c)); req.on("end", () => res(d));
  });

  // Ввод в одну страницу — строго по очереди. Иначе набор и мышь лезут в неё разом,
  // Playwright ждёт каждую операцию, и при быстром наборе с телефона это выглядит как
  // намертво зависшая страница. Очередь на страницу, а не глобальная: соседние сессии
  // друг друга не тормозят.
  const хвосты = new WeakMap();
  const поОчереди = (page, дело) => {
    const пред = хвосты.get(page) || Promise.resolve();
    const мой = пред.then(дело, дело).catch(() => { });
    хвосты.set(page, мой);
    return мой;
  };

  const handler = async (req, res) => {
    if (!(await checkCF(req).catch(() => false))) {
      res.writeHead(403, { "content-type": "text/plain; charset=utf-8" });
      return res.end("нет удостоверения Cloudflare Access");
    }
    const u = req.url.split("?")[0];
    try {
      if (u === "/") {
        await scan();
        const живых = [...keys.values()].filter((r) => !r.solved).length;
        res.writeHead(200, { "content-type": "text/plain; charset=utf-8" });
        return res.end("пульт жив. открытых проверок: " + живых + "\n");
      }
      if (u === "/pending") {
        await scan();
        const list = [...keys.entries()].filter(([, r]) => !r.solved).map(([k, r]) => ({
          ссылка: BASE + "/c/" + k,
          повод: r.повод || "",
          пауза: !!r.paused,
          ждёт: Math.round((Date.now() - r.born) / 1000),
          адрес: r.page.url().slice(0, 80),
        }));
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        return res.end(JSON.stringify(list));
      }
      if (u === "/debug") {
        const list = [];
        for (const p of pages()) {
          list.push({ адрес: p.url().slice(0, 70), кадры: (await адресаКадров(p)).map((f) => f.slice(0, 70)).filter(Boolean) });
        }
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        return res.end(JSON.stringify(list, null, 1));
      }
      // Заявка от сессии: «я упёрлась, нужен человек». Ловит любую проверку, включая
      // ту, у которой нет ни адреса, ни узнаваемого кадра. Снятие — тем же вызовом с
      // полем «снять»: сессия сообщает, что прошла дальше.
      if (u === "/register") {
        const j = JSON.parse((await body(req)) || "{}");
        // Сперва по идентификатору вкладки: две сессии могут стоять на одном адресе, и
        // тогда поиск по адресу выдал бы обеим одну и ту же вкладку. Адрес — запасной путь.
        let цель = null;
        if (j.цель && j.цель !== "-") {
          for (const p of pages()) {
            try {
              const info = await (await cdp(p)).send("Target.getTargetInfo");
              if (info && info.targetInfo && info.targetInfo.targetId === j.цель) { цель = p; break; }
            } catch (e) { /* вкладка успела закрыться — идём дальше */ }
          }
        }
        цель = цель
          || pages().find((p) => p.url() === j.адрес)
          || pages().find((p) => j.адрес && p.url().indexOf(j.адрес) === 0);
        if (!цель) { res.writeHead(404, { "content-type": "application/json; charset=utf-8" }); return res.end(JSON.stringify({ ошибка: "вкладка не найдена" })); }
        if (j.снять) {
          заявленные.delete(цель);
          await scan();
          res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
          return res.end(JSON.stringify({ снято: true }));
        }
        заявленные.add(цель);
        const k = mint(цель, j.повод || "нужна помощь");
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        return res.end(JSON.stringify({ ссылка: BASE + "/c/" + k }));
      }

      const m = /^\/c\/([A-Za-z0-9_-]+)(\/[a-z]+)?$/.exec(u);
      if (!m) { res.writeHead(404); return res.end("нет такой ручки"); }
      const rec = keys.get(m[1]);
      const what = m[2] || "";
      if (!rec) {
        res.writeHead(what ? 410 : 200, { "content-type": "text/html; charset=utf-8" });
        return res.end(what ? "" : DONE_HTML);
      }
      if (rec.solved && what) { res.writeHead(410); return res.end(""); }
      if (!what) {
        res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        return res.end(rec.solved ? DONE_HTML : PAGE_HTML);
      }
      const page = rec.page;

      // Долгое ожидание для агента: запрос висит, пока человек не нажмёт «Вернуть
      // агенту» или не пройдёт проверку. Сессия к этому моменту уже завершилась —
      // следить за нажатием было некому, и оно терялось. Теперь ждёт пульт, а агента
      // будит сам ответ.
      if (what === "/watch") {
        if (rec.paused || rec.solved) {
          res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
          return res.end(JSON.stringify({ исход: rec.solved ? "решено" : "отдано", адрес: rec.page.url() }));
        }
        const исход = await new Promise((готово) => {
          const t = setTimeout(() => готово(null), 10 * 60 * 1000);
          rec.ждущие = rec.ждущие || [];
          rec.ждущие.push((что) => { clearTimeout(t); готово(что); });
        });
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        return res.end(JSON.stringify({
          исход: исход || "ожидание истекло",
          адрес: rec.page.isClosed() ? "" : rec.page.url(),
        }));
      }
      if (what === "/state") {
        res.writeHead(200, { "content-type": "application/json; charset=utf-8" });
        return res.end(JSON.stringify({
          пройдена: !!rec.solved, пауза: !!rec.paused, повод: rec.повод || "",
          ждёт: Math.round((Date.now() - rec.born) / 1000),
        }));
      }
      // Возврат управления агенту: трансляция гасится, канал и мак не тратятся.
      if (what === "/stop") {
        rec.paused = true;
        await stopCast(page);
        // Метку кладём В САМУ СТРАНИЦУ: у сессии нет сетевого вызова (в песочнице
        // fetch отсутствует — замерено), и единственный канал до неё — браузер.
        // Пишем и в окно, и в хранилище вкладки: первое переживает работу, второе
        // перезагрузку. Хранилище именно вкладки, а не домена: localStorage у профиля
        // общий на все вкладки, и метка одной заявки доставалась бы чужим вкладкам того
        // же сайта — на этом контур уже обжёгся, ссылка отдавалась мгновенно.
        await page.evaluate(() => {
          window.__отдано = Date.now();
          try { sessionStorage.setItem("__отдано", String(Date.now())); } catch (e) { }
        }).catch(() => { });
        (rec.ждущие || []).forEach((fn) => fn("отдано")); rec.ждущие = [];
        console.log("управление возвращено агенту: " + m[1]);
        res.writeHead(204); return res.end();
      }
      if (what === "/resume") {
        rec.paused = false;
        await page.evaluate(() => {
          delete window.__отдано;
          try { sessionStorage.removeItem("__отдано"); } catch (e) { }
        }).catch(() => { });
        await startCast(page);
        res.writeHead(204); return res.end();
      }
      if (rec.paused && what !== "/reload") { res.writeHead(409); return res.end(""); }

      if (what === "/frame") {
        await raise(page);
        const c = await startCast(page);
        const since = Number(new URL(req.url, "http://x").searchParams.get("since") || 0);
        if (c.seq <= since || !c.buf) {
          await new Promise((doneFn) => {
            const t = setTimeout(doneFn, 8000);
            c.waiting.push(() => { clearTimeout(t); doneFn(); });
          });
        }
        if (rec.paused) { res.writeHead(409); return res.end(""); }
        if (!c.buf) { res.writeHead(204); return res.end(); }
        res.writeHead(200, {
          "content-type": "image/jpeg", "cache-control": "no-store",
          "content-length": c.buf.length, "x-seq": String(c.seq),
        });
        return res.end(c.buf);
      }
      // Обход всех вкладок на КАЖДОЕ нажатие был дорогим до неприличия — он ходит в
      // браузер за деревом кадров. Теперь его делает только фоновый обход раз в
      // полторы секунды: на скорость реакции это не влияет, а страница не встаёт.
      if (what === "/input") {
        const j = JSON.parse((await body(req)) || "{}");
        const шаги = j.шаги || (j.type ? [j] : []);
        const vp = await viewport(page);
        await поОчереди(page, async () => {
          for (const шаг of шаги) {
            const x = Math.round(шаг.x * vp.w), y = Math.round(шаг.y * vp.h);
            if (шаг.type === "down") { await page.mouse.move(x, y); await page.mouse.down(); }
            else if (шаг.type === "move") await page.mouse.move(x, y);
            else if (шаг.type === "up") { await page.mouse.move(x, y); await page.mouse.up(); }
          }
        });
        raised = 0;
        res.writeHead(204); return res.end();
      }
      if (what === "/scroll") {
        const j = JSON.parse((await body(req)) || "{}");
        const vp = await viewport(page);
        await поОчереди(page, async () => {
          await page.mouse.move(Math.round(vp.w / 2), Math.round(vp.h / 2));
          await page.mouse.wheel(Number(j.dx) || 0, Number(j.dy) || 0);
        });
        res.writeHead(204); return res.end();
      }
      if (what === "/key") {
        const j = JSON.parse((await body(req)) || "{}");
        await поОчереди(page, async () => {
          if (j.клавиша) await page.keyboard.press(String(j.клавиша));
          else if (j.текст) await page.keyboard.insertText(String(j.текст));
        });
        res.writeHead(204); return res.end();
      }
      if (what === "/reload") {
        await page.reload({ waitUntil: "domcontentloaded" });
        res.writeHead(204); return res.end();
      }
      res.writeHead(404); res.end("нет такой ручки");
    } catch (e) {
      res.writeHead(500); res.end(String(e && e.message ? e.message : e));
    }
  };

  // Слушаем простой http на localhost: наружу пульт отдаёт tailscale serve, он же держит TLS.
  // Своего https здесь нет намеренно — до пульта мимо serve дойти неоткуда.
  const srv = http.createServer(handler);
  srv.listen(LISTEN_PORT, HOST, () => {
    console.log("пульт слушает http://" + HOST + ":" + LISTEN_PORT + "/ (Chrome на " + CDP_PORT + ")");
  });
})().catch((e) => { console.error("пульт не поднялся:", e.message); process.exit(1); });
