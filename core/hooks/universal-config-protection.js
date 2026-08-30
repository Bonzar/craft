#!/usr/bin/env node
// Guard for file mutations: блокирует правку СУЩЕСТВУЮЩИХ конфигов
// линтеров и форматтеров. Агент, у которого не проходит проверка, склонен
// «чинить» конфиг вместо кода — гейт разворачивает его обратно к коду.
//
// Покрытие идёт по имени файла и перечислено ниже. Конфиг проекта на питоне
// НАМЕРЕННО не гейтится: там метаданные проекта вперемешку с настройками
// линтера, и блок ломал бы законные правки зависимостей.
//
// Создание НОВОГО конфига (файла нет на диске) разрешено: это сетап проекта, а
// не ослабление проверок. Байпаса через окружение намеренно нет: файловые
// инструменты не несут команды, куда можно было бы вписать маркер, — обход
// только явным разрешением Влада на конкретный вызов.
//
// Fail open на всём неожиданном: сломанный гейт не должен клинить работу.
import path from 'node:path';
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { deny } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { canonicalPatchChanges } from '../contracts/action.mjs';

const PROTECTED = [
  /^\.eslintrc$/, /^\.eslintrc\./, /^eslint\.config\./,
  /^\.prettierrc$/, /^\.prettierrc\./, /^prettier\.config\./,
  /^biome\.json/,
  /^\.stylelintrc$/, /^\.stylelintrc\./, /^stylelint\.config\./,
  /^ruff\.toml$/, /^\.ruff\.toml$/,
  /^\.editorconfig$/,
];

const { raw, event, route, input } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

const files = route === 'file.patch'
  ? (canonicalPatchChanges(input) || []).flatMap((change) => [change.file, change.destination].filter(Boolean))
  : [input.target || ''];
const file = files.find((candidate) => {
  if (!candidate || !fs.existsSync(candidate)) return false;
  return PROTECTED.some((re) => re.test(path.basename(candidate)));
});
if (!file) process.exit(0);

deny(`Заблокировано: правка конфига линтера/форматтера (${file}). Чини код, а не ослабляй конфиг — падающая проверка указывает на код, правило под неё не подгоняется. Правка конфига — только по явной просьбе Влада. Влад явно попросил поменять конфиг → скажи об этом в ответе и попроси его нажать allow на этот вызов.`);
