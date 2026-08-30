// Решение, класть ли вход codex из настроек окружения поверх своего файла.
// Цена ошибки несимметрична: не положить — сессия останется без codex и это
// видно сразу; положить лишнего — откатить уже обновлённый токен к старому и
// сломать вход молча. Поэтому решение вынесено отдельной функцией и держится
// проверками, а не чтением кода хука.
//
// Расширение .mjs, а не .js: в каталоге тестов нет манифеста модулей, и .js
// читался бы как обычный скрипт, которому импорт недоступен.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { decideCodexAuth } = await import('../../adapters/codex/hooks/lib/auth.js');

// Вход из настроек окружения: та же форма, что у файла клиента, — важны только
// наличие токенов и дата обновления.
const iz = (last) => JSON.stringify({
  auth_mode: 'chatgpt',
  tokens: { id_token: 'i', access_token: 'a', refresh_token: 'r', account_id: 'c' },
  last_refresh: last,
});

test('своего файла нет — кладём', () => {
  const d = decideCodexAuth(iz('2026-08-28T05:35:31Z'), null);
  assert.equal(d.write, true);
});

test('свой файл старее — кладём', () => {
  const d = decideCodexAuth(iz('2026-08-28T05:35:31Z'), iz('2026-08-01T00:00:00Z'));
  assert.equal(d.write, true);
});

test('свой файл новее — не трогаем', () => {
  // Сессия уже обновила токен сама: запись поверх откатила бы вход к старому.
  const d = decideCodexAuth(iz('2026-08-01T00:00:00Z'), iz('2026-08-28T05:35:31Z'));
  assert.equal(d.write, false);
});

test('свой файл той же свежести — не трогаем', () => {
  const t = '2026-08-28T05:35:31Z';
  assert.equal(decideCodexAuth(iz(t), iz(t)).write, false);
});

test('свой файл битый — кладём поверх', () => {
  // «Старее или новее» тут не вычисляется, а негодный вход не лучше никакого.
  for (const svoy of ['{ полом', '{}', JSON.stringify({ tokens: { access_token: 'a' } })]) {
    assert.equal(decideCodexAuth(iz('2026-08-28T05:35:31Z'), svoy).write, true, `свой: ${svoy}`);
  }
});

test('в переменной не JSON — не трогаем ничего', () => {
  for (const znachenie of ['', '   ', 'не json', '{ обрыв']) {
    const d = decideCodexAuth(znachenie, null);
    assert.equal(d.write, false, `значение: ${znachenie}`);
    assert.match(d.why, /разобрать|пуст/i, 'причина названа');
  }
});

test('в переменной JSON без токенов — не трогаем ничего', () => {
  for (const znachenie of ['{}', '[]', '"строка"', JSON.stringify({ tokens: {} })]) {
    assert.equal(decideCodexAuth(znachenie, null).write, false, `значение: ${znachenie}`);
  }
});

test('дата обновления отсутствует у обоих — кладём', () => {
  // Сравнивать нечем, а свой файл при этом мог быть заведён чем угодно.
  const bez = JSON.stringify({ tokens: { access_token: 'a', refresh_token: 'r' } });
  assert.equal(decideCodexAuth(bez, bez).write, true);
});
