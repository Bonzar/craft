// Уборка старых файлов состояния в каталоге слоя.
//
// Состояние хуков живёт во временном каталоге и переживает сессию: метки уступки,
// журналы решений. Ни у одного из них нет владельца, который снял бы их за собой,
// — процесс, написавший последнюю строку, о конце сессии не знает. Без уборки они
// копятся до конца жизни машины, по файлу (а у меток — по каталогу) на каждый
// вызов каждого хука.
//
// Возраст, а не протухание: файл, которого не касались дольше срока, принадлежит
// сессии, которая уже кончилась. Живой сессии сноса не грозит — её файлы моложе
// срока, потому что каждое событие их дописывает.
//
// Уборка НЕ на каждом вызове: каталог сканируется не чаще раза в `everyMs`, и
// право на скан забирает тот, кто обновил файл-отметку. Отметка лежит в том же
// каталоге и под тот же разбор не попадает — иначе уборщик снёс бы сам себя.
import fs from 'node:fs';
import path from 'node:path';

export function sweepOld(dir, {
  prefix, stamp, ttlMs, everyMs,
}) {
  if (!dir || !prefix || !stamp) return false;
  const stampFile = path.join(dir, stamp);
  try {
    if (Date.now() - fs.statSync(stampFile).mtimeMs < everyMs) return false;
  } catch { /* отметки ещё нет — убираем и заводим её */ }
  try {
    fs.writeFileSync(stampFile, '');
  } catch {
    return false; // каталог не пишется — уборка не наше дело
  }
  let names = [];
  try {
    names = fs.readdirSync(dir);
  } catch {
    return false;
  }
  const now = Date.now();
  for (const name of names) {
    if (!name.startsWith(prefix) || name === stamp) continue;
    const file = path.join(dir, name);
    try {
      if (now - fs.statSync(file).mtimeMs > ttlMs) fs.rmSync(file, { recursive: true, force: true });
    } catch { /* файл пропал сам */ }
  }
  return true;
}
