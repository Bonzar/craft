// Пропуск: браузер проходит антибот, а тела забираются сырыми.
//
// Скрипт ничего не знает про конкретный сайт. Ему называют адрес навигации и
// список запросов; он открывает адрес обычным заходом — этим и проходится
// проверка, — а дальше выполняет запросы ИЗНУТРИ уже открытой страницы и отдаёт
// тела как есть.
//
// Почему изнутри страницы, а не чтением самой страницы: запрос возвращает сырой
// серверный HTML, побайтово как из сети, а готовая страница браузера приходит
// сериализованной — кавычки в атрибутах экранированы одинарно, амперсанд
// двойным, и выражения вызывающего такую страницу уже не читают.
//
// Задание кладётся объявлением JOB перед этим файлом:
//   { nav: "https://…", requests: [ { url, method, headers } ] }
//
// Наружу уходит одна строка «KINOWATCH_PASS <json>»: пульт печатает в тот же
// поток и своё, и чужое, поэтому тела ищутся по маркеру, а не по первой строке.

const out = { responses: [], notes: [] };

// Признаки того, что перед нами проверка, а не страница. Список намеренно
// короткий: длинный ловил бы обычные страницы, где эти слова просто написаны.
const ПРОВЕРКА = /ddos-guard|showcaptcha|xpvnsulc|SmartCaptcha|Проверка браузера|Access denied|Attention Required/i;

const page = await browser.newPage();
try {
  const resp = await page.goto(JOB.nav, { waitUntil: 'domcontentloaded', timeout: 90000 });
  const код = resp ? resp.status() : 0;
  const html = await page.content();
  out.notes.push('навигация: код ' + код + ', ' + html.length + ' байт');

  // Упёрлись в проверку — зовём человека. Сам по себе зов не сработает: контур
  // готов, но выкрикнуть его должен скрипт. Ожидание идёт БЕЗ СРОКА: машина не
  // знает, когда человек освободится, а таймер превратил бы «ещё не пришёл» в
  // «отказал».
  if (код === 403 || код === 429 || ПРОВЕРКА.test(html)) {
    out.notes.push('упёрлись в проверку, зову человека');
    await ohelp(page, 'антибот не пропустил: код ' + код);
    const исход = await owait(page);
    out.notes.push('человек: ' + исход);
    await odone(page);
  }

  for (const req of JOB.requests) {
    const got = await page.evaluate(async function (r) {
      try {
        const ответ = await fetch(r.url, {
          method: r.method || 'GET',
          headers: r.headers || {},
          credentials: 'include',
        });
        return { status: ответ.status, body: await ответ.text() };
      } catch (e) {
        return { status: 0, body: '', err: String((e && e.message) || e) };
      }
    }, req);

    out.responses.push({ url: req.url, status: got.status, body: got.body, err: got.err });
  }
} catch (e) {
  out.notes.push('пропуск оборвался: ' + String((e && e.message) || e));
} finally {
  await page.close();
}

console.log('KINOWATCH_PASS ' + JSON.stringify(out));
