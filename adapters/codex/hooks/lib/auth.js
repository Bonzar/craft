// Решение, класть ли вход codex из настроек окружения поверх своего файла.
//
// Вход в codex целиком лежит в одном файле auth.json, и это делает его
// переносимым: положил файл в пустой дом — клиент авторизован. На этом и
// держится шеринг между сессиями, у каждой из которых свой контейнер.
//
// Цена ошибки несимметрична, поэтому решение вынесено сюда, а не размазано по
// хуку. Не положить вход — сессия останется без codex, и это видно сразу же.
// Положить лишнего — откатить уже обновлённый токен к старому из настроек, и
// вход сломается молча, через несколько дней, без всякой связи с причиной.
//
// Свежесть считается по last_refresh ВНУТРИ файла, а не по времени изменения на
// диске: файл кладёт хук, и его mtime говорит о моменте копирования, а не о
// свежести токена.

// Разобрать вход: годным считается объект с непустыми токенами. Всё прочее —
// мусор, которым перекрывать рабочий вход нельзя.
function razobrat(text) {
  if (!text || !String(text).trim()) return null;
  let d;
  try {
    d = JSON.parse(text);
  } catch {
    return null;
  }
  if (!d || typeof d !== 'object' || Array.isArray(d)) return null;
  const t = d.tokens;
  if (!t || typeof t !== 'object') return null;
  if (!t.access_token && !t.refresh_token) return null;
  return d;
}

// decideCodexAuth(значение переменной, содержимое своего файла или null)
//   → { write, why }
export function decideCodexAuth(izPeremennoy, svoyFile) {
  const iz = razobrat(izPeremennoy);
  if (!iz) return { write: false, why: 'вход из окружения пуст или его не разобрать' };

  const svoy = razobrat(svoyFile);
  // Своего нет или он негодный: негодный вход не лучше никакого, и «старее или
  // новее» на нём не вычисляется.
  if (!svoy) return { write: true, why: 'своего входа нет или он не читается' };

  const a = svoy.last_refresh;
  const b = iz.last_refresh;
  // Дат нет ни у кого — сравнивать нечем; свой файл при этом мог быть заведён
  // чем угодно, а тот, что дал Влад, заведомо рабочий.
  if (!a && !b) return { write: true, why: 'даты обновления нет ни у одного из них' };
  if (!a) return { write: true, why: 'у своего входа нет даты обновления' };
  if (!b) return { write: false, why: 'у входа из окружения нет даты обновления' };

  const tSvoy = Date.parse(a);
  const tIz = Date.parse(b);
  if (Number.isNaN(tSvoy)) return { write: true, why: 'дату своего входа не разобрать' };
  if (Number.isNaN(tIz)) return { write: false, why: 'дату входа из окружения не разобрать' };

  if (tSvoy < tIz) return { write: true, why: 'свой вход старее' };
  return { write: false, why: 'свой вход не старее — оставляем его' };
}
