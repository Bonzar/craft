// Формула возможности живёт НА ДВУХ ЯЗЫКАХ, и это кейс про их согласие.
//
// Возможность выводится из `name` и `for` (решение 6). Спрашивают её двое:
// обёртка пакета, когда резолвит `requires` (`runtime/pylib/decision.py`), и
// JS-слой, когда ищет реализацию разбора команды
// (`.claude/hooks/lib/write-targets-bash.js`). Разъедься они — реализация
// нашлась бы на одной стороне и потерялась на другой, и план-гейт молча начал
// бы сверять КАЖДЫЙ вызов шелла, потому что дерева ему больше не дают.
//
// Кейс гоняет обе реализации по одной таблице. Так же, одной таблицей, держится
// формула ключа следа (key-parity.test.mjs) — по той же причине и с тем же
// уроком: пара, у которой нет общего кейса, расходится молча.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const { capabilityOf } = await import('../../.claude/hooks/lib/write-targets-bash.js');

// Пары «манифест → возможность». Каждая строка отвечает на свой вопрос:
// снимается ли хвост инструмента, снимается ли хвост харнеса, переживает ли
// имя без хвоста, не срезается ли дефис у самостоятельного пакета.
const TABLE = [
  { name: 'command-tree-shell', for: 'tool:shell' },
  { name: 'command-tree-fish', for: 'tool:fish' },
  { name: 'scope-codex', for: 'harness:codex' },
  { name: 'trace-probe', for: 'general' },
  { name: 'changeset-review', for: 'general' },
  { name: 'guard-irreversible', for: 'general' },
  // Хвост, не совпавший с инструментом из `for`, не снимается.
  { name: 'command-tree-shell', for: 'tool:bash' },
  // Пустой и кривой `for` ответа не ломают.
  { name: 'проба', for: '' },
];

function pythonSide(rows) {
  const code = [
    'import json, sys',
    `sys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'runtime', 'pylib'))})`,
    'from decision import capability_of',
    'rows = json.load(sys.stdin)',
    'print(json.dumps([capability_of(r["name"], r["for"]) for r in rows]))',
  ].join('\n');
  const done = spawnSync('python3', ['-c', code], { input: JSON.stringify(rows), encoding: 'utf8' });
  assert.equal(done.status, 0, `питон не ответил: ${done.stderr}`);
  return JSON.parse(done.stdout);
}

test('возможность выводится одинаково на обеих сторонах', () => {
  const js = TABLE.map(capabilityOf);
  assert.deepEqual(js, pythonSide(TABLE), 'формулы разъехались');
  // И сама таблица должна что-то утверждать: ответы не все одинаковы, а
  // ключевые — те, ради которых формула и заведена, — названы прямо.
  assert.equal(capabilityOf(TABLE[0]), 'command_tree');
  assert.equal(capabilityOf(TABLE[2]), 'scope');
  assert.equal(capabilityOf(TABLE[4]), 'changeset_review');
  assert.equal(capabilityOf(TABLE[6]), 'command_tree_shell');
});
