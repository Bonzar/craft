// Shared content helpers. Harness transcript parsing belongs to adapters.
import fs from 'node:fs';

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

// Материал источника вместе с тем, на что Влад отвечает. Формой пользуются оба
// источника — реплика и ответ кнопкой, — поэтому она живёт здесь, а не копией в
// каждом хуке: разъехавшиеся формулировки дали бы разбору два разных материала
// на одинаковый по смыслу вход.
//
// Контекста нет — уходит одно сказанное. Обрезки нет: срез отрезал бы согласие.
export function withAgentContext(context, said) {
  if (!context) return said;
  return `Последнее сообщение агента (на него отвечает Влад):\n\n${context}\n\nСказанное Владом:\n\n${said}`;
}
