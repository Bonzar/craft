// «Не задано» — это ОТСУТСТВИЕ ключа, а не пустое значение.
//
// Пустую строку ставят намеренно: «этого канала у меня нет, в сеть не ходи».
// Перебивать её личным файлом доступа значит отменять решение вызывающего — так
// фикстура сводки, погасившая доступ к connect-API, получала его обратно и шла в
// сеть, а хук входа в codex доходил до глобальной установки пакета. Проверка
// именно поимённая: одиннадцатое ревью показало, что герметичность держалась не
// на тех выключателях, которые названы в шапке.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const { loadEnv } = await import('../../.claude/hooks/lib/env.js');

function withEnv(vars, body) {
  const saved = new Map();
  for (const key of Object.keys(vars)) saved.set(key, Object.hasOwn(process.env, key) ? process.env[key] : undefined);
  try {
    for (const [key, value] of Object.entries(vars)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    body();
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

function personalFile(text) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'env-unset-test.'));
  const file = path.join(dir, 'craft.env');
  fs.writeFileSync(file, text);
  return { dir, file };
}

test('пустое значение НЕ перебивается личным файлом доступа', () => {
  const { dir, file } = personalFile('CRAFT_API_BASE=https://пример.невалид/api\nCODEX_AUTH_JSON={"подделка":1}\n');
  try {
    withEnv({ CRAFT_API_BASE: '', CODEX_AUTH_JSON: '' }, () => {
      loadEnv({ root: dir, personalEnv: file });
      assert.equal(process.env.CRAFT_API_BASE, '', 'погашенный доступ обязан остаться погашенным');
      assert.equal(process.env.CODEX_AUTH_JSON, '', 'погашенный вход обязан остаться погашенным');
    });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('отсутствующее значение личным файлом ДОБИРАЕТСЯ — иначе он был бы бесполезен', () => {
  const { dir, file } = personalFile('CRAFT_API_BASE=https://пример.невалид/api\n');
  try {
    withEnv({ CRAFT_API_BASE: undefined }, () => {
      loadEnv({ root: dir, personalEnv: file });
      assert.equal(process.env.CRAFT_API_BASE, 'https://пример.невалид/api');
    });
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
