// Тело ошибки из ответа инструмента. Форма ответа — форма харнеса, и разбирает её
// обёртка; юнитов у неё не было вовсе, и в этой дыре жили два дефекта сразу —
// пустое содержимое съедало непустую ошибку, а ненайденное тело подменялось
// JSON-дампом всего ответа.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { errorText, responseIsError } from '../../.claude/hooks/lib/event-claude.js';

test('тело ошибки достаётся из любой формы ответа', () => {
  assert.equal(errorText('Error: ENOENT'), 'Error: ENOENT', 'ответ строкой');
  assert.equal(errorText({ content: 'вызов не состоялся' }), 'вызов не состоялся');
  assert.equal(errorText({ is_error: true, content: 'упало' }), 'упало');
  assert.equal(errorText({ error: 'boom' }), 'boom');
  assert.equal(errorText([{ type: 'text', text: 'боль' }]), 'боль', 'ответ блоками');
  assert.equal(errorText({ content: [{ text: 'вложенный' }] }), 'вложенный');
  // Блок, назвавший тело `content`, а не `text`: запасная ветка разбора блока
  // своей строки не имела вовсе, и снятая она проходила молча.
  assert.equal(errorText([{ content: 'тело блока' }]), 'тело блока');
});

test('пустое содержимое не съедает непустую ошибку', () => {
  // `??` проваливается только на null и undefined: пустая строка в содержимом
  // выбрасывала текст, который лежал рядом.
  assert.equal(errorText({ content: '', error: 'нет такого файла' }), 'нет такого файла');
  assert.equal(errorText({ content: {}, error: 'boom' }), 'boom');
  assert.equal(errorText({ content: [], error: 'boom' }), 'boom');
});

test('тела нет — пусто, а не выдумка', () => {
  // Свалить сюда JSON всего ответа нельзя дважды: это подделка на месте имени
  // недостающего, и через неё в журнал уезжало бы содержимое ответа инструмента.
  assert.equal(errorText({ is_error: true }), '');
  assert.equal(errorText({ content: {} }), '');
  assert.equal(errorText({ content: [{ type: 'image', source: { data: 'секрет' } }] }), '');
  assert.equal(errorText(null), '');
  assert.equal(errorText(undefined), '');
  assert.equal(errorText({ error: false }), '');
});

test('«была ли ошибка» и «есть ли тело» — разные вопросы', () => {
  // Признак ошибки при пустом теле — законная пара: провал случился, текста нет,
  // и журнал называет это именем, а не молчит про сигнал.
  assert.equal(responseIsError({ is_error: true }), true);
  assert.equal(errorText({ is_error: true }), '');
  // И наоборот: тело есть, а признака нет — так приходит провал ОТДЕЛЬНЫМ
  // событием, и текст обязан достаться из ответа всё равно.
  assert.equal(responseIsError({ content: 'вызов не состоялся' }), false);
  assert.equal(errorText({ content: 'вызов не состоялся' }), 'вызов не состоялся');
});

// Формы ответа, на которых разбор тела ошибки терял текст или падал.
test('тело ошибки достаётся и из списка строк, и из вложенного списка', () => {
  assert.equal(errorText(['boom', 'again']), 'boom\nagain', 'список строк');
  assert.equal(errorText([[{ text: 'deep' }]]), 'deep', 'список списков');
});

test('булево телом не является, а цикл не роняет разбор', () => {
  // `{error: true}` значит «ошибка была»; выдать за текст слово «true» — та же
  // подделка, что и JSON-дамп.
  assert.equal(errorText({ error: true }), '');
  // Ответ приходит извне, и звать разбор без перехвата можно только если он сам
  // не уходит в бесконечность.
  const looped = {};
  looped.content = looped;
  assert.equal(errorText(looped), '');
  let deep = { content: 'дно' };
  for (let i = 0; i < 5000; i += 1) deep = { content: deep };
  assert.doesNotThrow(() => errorText(deep));
});
