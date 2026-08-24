// Мелочи об окружении, которые в шелле были встроенными словами, а здесь
// требуют кода.
import fs from 'node:fs';
import path from 'node:path';

// Есть ли команда в PATH — то же, что делало `command -v`: перебор каталогов
// PATH и проверка исполняемого бита. Спрашивать саму команду запуском нельзя:
// у части из них запуск не бесплатный.
export function hasCommand(name) {
  for (const dir of (process.env.PATH || '').split(path.delimiter)) {
    if (!dir) continue;
    try {
      fs.accessSync(path.join(dir, name), fs.constants.X_OK);
      return true;
    } catch { /* в этом каталоге команды нет */ }
  }
  return false;
}

// Метка времени формата `date -u +%FT%TZ` — без долей секунды.
export function utcStamp() {
  return new Date().toISOString().replace(/\.\d+Z$/, 'Z');
}
