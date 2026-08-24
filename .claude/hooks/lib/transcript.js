// Чтение транскрипта сессии. Файл — JSONL: строка на запись, внутри записи
// сообщение, а в его содержимом вызовы инструментов.
//
// Стоп-хукам нужен один и тот же факт: какие файлы правились за сессию. Список
// собран здесь, потому что по нему судят два гейта — качества и отладочных
// логов, — и разъехавшиеся копии дали бы гейты, проверяющие разные наборы.
import fs from 'node:fs';

const WRITE_TOOLS = ['Edit', 'Write', 'MultiEdit'];

// Пути файлов из вызовов записи, отсортированные и без повторов. Битая строка
// пропускается — она не должна ронять весь разбор.
export function editedFiles(file) {
  let text = '';
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return [];
  }
  const found = new Set();
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      continue;
    }
    const content = entry && entry.message && entry.message.content;
    if (!Array.isArray(content)) continue;
    for (const item of content) {
      if (!item || item.type !== 'tool_use') continue;
      if (!WRITE_TOOLS.includes(item.name)) continue;
      const path = item.input && item.input.file_path;
      if (typeof path === 'string' && path) found.add(path);
    }
  }
  return [...found].sort();
}

// Отбор файлов, за которые отвечают гейты кода: существующие исходники
// JS/TS вне зависимостей и тестов.
export function sourceFiles(files) {
  return files.filter((file) => {
    if (!fs.existsSync(file)) return false;
    if (file.includes('node_modules/')) return false;
    if (/\.test\.|\.spec\.|__tests__/.test(file)) return false;
    return /\.(ts|tsx|js|jsx)$/.test(file);
  });
}
