#!/usr/bin/env node
// SessionStart hook: печатает в контекст карту возможностей ЭТОЙ сессии — что
// из инфраструктуры доступно, что нет. Ответ на требование «агент всегда
// понимает, что он может, а что нет»: недоступное не предлагается и не
// имитируется, а называется недоступным с указанием, где задача выполнима.
//
// Детект дешёвый и честный: только то, что проверяемо из окружения (платформа,
// CLI, переменные). Наличие MCP-серверов хуку не видно — по ним карта даёт
// ожидание по окружению, а истина — список инструментов сессии.
//
// Fail quiet: любая ошибка не должна ломать старт сессии.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { readEvent } from './lib/event.js';
import { hookOnce } from './lib/once.js';
import { loadEnv } from './lib/env.js';
import { hasCommand } from './lib/system.js';

// Project-уровень уступает user-уровню (install.sh), иначе карта печатается
// дважды в craft-сессиях на локальной машине.
const { raw, event } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

loadEnv();

const platform = os.platform() === 'darwin' ? 'локальный мак' : 'облачный контейнер (Linux)';

const home = os.homedir();
const arcadia = hasCommand('arc')
  && (fs.existsSync(path.join(home, 'arcadia')) || fs.existsSync(path.join(home, 'arc-mounts')));

const arcLine = arcadia
  ? 'доступен — задачи Аркадии выполнимы (изолированный маунт, скилл arc-temp-branch-pr)'
  : 'НЕДОСТУПЕН — работа с кодом Аркадии в этой сессии невозможна, только с локальной машины';
const trackerLine = arcadia
  ? 'ожидается подключённым (истина — список инструментов сессии: mcp__startrek…)'
  : 'НЕДОСТУПЕН — Яндекс-трекер только с локальной машины';
const craftLine = process.env.CRAFT_API_BASE
  ? 'connect-API доступен; дополнительные возможности чтения и записи объявляет адаптер'
  : 'connect-API не сконфигурирован в этой сессии; при наличии Craft MCP работай через него';

process.stdout.write(`=== Карта окружения сессии ===
- Платформа: ${platform}
- arc / Аркадия: ${arcLine}
- Яндекс-трекер MCP: ${trackerLine}
- Craft: ${craftLine}
Директива: недоступное здесь — не предлагать, не имитировать и не заменять
выдумкой; если задача требует недоступного, скажи явно и назови, где она
выполнима (локальная машина / облачная craft-сессия).
=== конец карты ===
`);
