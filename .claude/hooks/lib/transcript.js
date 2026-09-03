// Чтение транскрипта сессии. Файл — JSONL: строка на запись, внутри записи
// сообщение, а в его содержимом вызовы инструментов.
//
// Стоп-хукам нужен один и тот же факт: какие файлы правились за сессию. Список
// собран здесь, потому что по нему судят два гейта — качества и отладочных
// логов, — и разъехавшиеся копии дали бы гейты, проверяющие разные наборы.
import fs from 'node:fs';
import { eachJsonl } from './jsonl.js';

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
  eachJsonl(text, (entry) => {
    const content = entry.message && entry.message.content;
    if (!Array.isArray(content)) return;
    for (const item of content) {
      if (!item || item.type !== 'tool_use') continue;
      if (!WRITE_TOOLS.includes(item.name)) continue;
      const path = item.input && item.input.file_path;
      if (typeof path === 'string' && path) found.add(path);
    }
  });
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

// Текст последнего сообщения агента. Короткая реплика Влада — «убирай хвосты»,
// «да, делай», «согласен» — осмысленна только вместе с тем, на что отвечает:
// сам по себе её текст не называет ни работы, ни адресов, и разбор материала
// не соберёт из него ни цели, ни задачи. Перечень же лежит в ответе агента
// прямо над репликой.
//
// Берётся ПОСЛЕДНЕЕ сообщение роли assistant с текстовым содержимым: записи с
// одними вызовами инструментов пропускаются — им не отвечают. Транскрипт
// читается с конца, поэтому длина файла на цену не влияет.
export function lastAssistantText(file) {
  let text = '';
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    return '';
  }
  const lines = text.split('\n');
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (!lines[i].trim()) continue;
    let entry;
    try {
      entry = JSON.parse(lines[i]);
    } catch {
      continue;
    }
    const message = entry && entry.message;
    if (!message || message.role !== 'assistant') continue;
    const content = message.content;
    if (typeof content === 'string' && content.trim()) return content.trim();
    if (!Array.isArray(content)) continue;
    const said = content
      .filter((item) => item && item.type === 'text' && typeof item.text === 'string')
      .map((item) => item.text)
      .join('\n')
      .trim();
    if (said) return said;
  }
  return '';
}

// Материал источника вместе с тем, на что Влад отвечает. Формой пользуются оба
// источника — реплика и ответ кнопкой, — поэтому она живёт здесь, а не копией в
// каждом хуке: разъехавшиеся формулировки дали бы разбору два разных материала
// на одинаковый по смыслу вход.
//
// Транскрипта нет, он не читается или в нём ещё нет ответов агента — уходит одно
// сказанное, как было раньше. Обрезки нет: срез отрезал бы согласие на середине.
export function withAgentContext(transcriptFile, said) {
  const context = transcriptFile ? lastAssistantText(transcriptFile) : '';
  if (!context) return said;
  return `Последнее сообщение агента (на него отвечает Влад):\n\n${context}\n\nСказанное Владом:\n\n${said}`;
}
