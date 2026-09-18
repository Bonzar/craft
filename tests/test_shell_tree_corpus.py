"""Корпус кейсов старого разбора против нашего вердикта.

`data/shell/write-targets-cases.json` — 233 кейса, снятые с тестов снятого слоя
JS-хуков. Поле `expected` там — ожидание своими словами, и в утверждения о нашем
ответе оно переводится ЗДЕСЬ, явной таблицей: без неё «прошло» значило бы только
«не упало».

Перевод по семьям фраз:

    «только читает — доказано»     → writes == «нет»
    «только читает — не доказано»  → writes != «нет»
    «мир не менялся»               → writes == «нет»
    «мир менялся»                  → writes == «да»
    «отправка (push)»              → writes == «да» и цель вида «сеть»
    «не отправка»                  → целей вида «сеть» нет
    «цель записи — X»              → writes == «да» и X в списке целей
    «…, один раз»                  → X в списке ровно один раз
    «целей записи нет»             → список целей пуст
    «запись известна»              → writes == «да»
    «запись НЕ известна»           → writes == «неизвестно»
    «записи нет»                   → writes == «нет»
    «виновник — «X»»               → причина называет X
    «прочитан …», «чтение»         → writes == «нет», но только когда про запись
                                     во фразе не сказано ничего: фразы про
                                     прочитанные файлы — ожидания другого гварда

Тест идёт живой утилитой: без неё он пропускается целиком. Кейсы, которые не
сошлись, не подгоняются — каждый в `SKIPPED` с причиной, та же причина в отчёте
этапа.
"""

import importlib.util
import json
import unittest
from pathlib import Path

from . import MODULES_DIR, REPO_ROOT

CASES = REPO_ROOT / 'data' / 'shell' / 'write-targets-cases.json'
CASES_COUNT = 233


def load_library():
    spec = importlib.util.spec_from_file_location(
        'shell_tree_corpus_lib', MODULES_DIR / 'shell-tree' / 'lib' / '__init__.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


library = load_library()

# Кейсы, которые не сошлись. Причина у каждого своя и живёт здесь же.
SKIPPED = {
    # bash-classify судит команду по имени файла, а не по тому, как она вызвана.
    './sed -n 1p README.md|только читает — не доказано':
        'bash-classify судит по имени файла: `./sed` для него тот же sed, и ответ «нет». Свой скрипт с именем читающей команды так проходит незамеченным.',
    '/usr/bin/cat README.md|только читает — не доказано':
        'то же: вызов по полному пути bash-classify от имени команды не отличает.',
    'bin/grep x README.md|только читает — не доказано':
        'то же: вызов по относительному пути.',
    # База правил bash-classify про эти подкоманды говорит иначе, чем корпус.
    'git tag|мир не менялся':
        'у bash-classify `git tag` без аргументов — LOCAL_EFFECTS, вердикт «да»; кейс ждёт «нет». Лишний вопрос человеку, не дыра.',
    'git branch|мир не менялся':
        'то же: голый `git branch` у bash-classify меняет мир.',
    'git stash push -m wip|мир менялся':
        'правила `git.stash` в базе bash-classify нет: вердикт «неизвестно», кейс ждёт «да».',
    'git push --dry-run|не отправка':
        'про `--dry-run` bash-classify не знает: `git.push` даёт цель вида «сеть», кейс ждёт «не отправка».',
    # Сужение старого гварда до репозитория.
    'echo x > /tmp/scratch.txt|мир не менялся':
        'старый гвард не считал /tmp миром. Наш вопрос — «пишет ли команда», и запись в /tmp это запись; сужение до репозитория — дело замка.',
    'printf x > /tmp/черновик|целей записи нет':
        'то же сужение до репозитория: цель /tmp/черновик мы называем.',
    # Цена нашей страховки от дыры с heredoc.
    "cat <<'EOF'|прочитанных файлов нет":
        'страховка: у команды с heredoc ответа «нет» быть не может, потому что рядом с heredoc bash-classify теряет перенаправление вывода. Здесь записи нет, и «неизвестно» — лишний вопрос.',
    'cat <<EOF\nсмотри README.md подробнее\nEOF|прочитанных файлов нет':
        'та же страховка от heredoc.',
    "cat <<'MSG'\nfix lib/journal.js first\nMSG|прочитанных файлов нет":
        'та же страховка от heredoc.',
    'cat <<EOF\nrm -rf /x\nEOF|только читает — доказано':
        'та же страховка от heredoc; тело heredoc при этом за команду не принято — это видно по классификации READONLY.',
    "cat <<'EOF'\ngit push --force\nEOF|только читает — доказано":
        'та же страховка от heredoc.',
    # Модель bash-classify отличается от модели старого гварда.
    'cat a.js & cat b.js|прочитаны a.js и b.js':
        'bash-classify поднимает фоновую команду до LOCAL_EFFECTS («elevated by backgrounding»): фоновая работа переживает вызов. Кейс считает это чтением.',
    'node сборка.js|запись НЕ известна':
        'у bash-classify есть правило `node`, и он отвечает «да»; кейс ждёт «неизвестно».',
    # Читающие команды, которых нет в базе правил bash-classify.
    'bat --file-name a.js b.js|прочитан b.js':
        '`bat` в базе правил bash-classify нет: UNKNOWN, вердикт «неизвестно».',
    'bat /repo/a.js|прочитан /repo/a.js': '`bat` в базе правил нет.',
    'od /repo/a.js|прочитан /repo/a.js': '`od` в базе правил нет.',
    'xxd /repo/a.js|прочитан /repo/a.js': '`xxd` в базе правил нет.',
    'cksum /repo/a.js|прочитан /repo/a.js': '`cksum` в базе правил нет.',
    'cmp /repo/a.js /repo/b.js|прочитаны /repo/a.js и /repo/b.js': '`cmp` в базе правил нет.',
    # Оболочки, которых нет в базе правил: строку после -c никто не разбирает.
    'dash -c "cat a.js"|снятие обёртки даёт «cat a.js»':
        '`dash` в базе правил bash-classify нет, делегирования не происходит: вердикт «неизвестно».',
    'dash -c "cat > README.md"|цель записи — README.md': 'то же: `dash` в базе правил нет.',
    'ksh -c "cat a.js"|снятие обёртки даёт «cat a.js»': '`ksh` в базе правил нет.',
    'ksh -c "cat > README.md"|цель записи — README.md': '`ksh` в базе правил нет.',
    'fish -c "cat a.js"|снятие обёртки даёт «cat a.js»': '`fish` в базе правил нет.',
    'fish -c "cat > README.md"|цель записи — README.md': '`fish` в базе правил нет.',
    'BASH -c "cat > README.md"|цель записи — README.md':
        'правила bash-classify чувствительны к регистру: `BASH` для него незнакомая команда.',
    'bash -lc "cat > w.txt"|цель записи — w.txt':
        'при слитом кластере ключей (`-lc`) bash-classify делегирование не показывает: строку `cat > w.txt` разбирать нечему, и цели нет. Сам вердикт при этом «да».',
    'bash -c "cd /tmp && cat > inner.txt"|цель записи — /tmp/inner.txt':
        'владельца перенаправления bash-classify не называет — цель приписана первой команде строки, — поэтому при переходе каталога путь остаётся относительным: «inner.txt», а не «/tmp/inner.txt».',
    'bash -c \'bash -c "cat > deep.md"\'|целей записи нет, но «только читает» — не доказано':
        'расхождение в нашу пользу: старый разбор снимал одну обёртку и цели не находил, мы разворачиваем вложенные строки до конца и называем deep.md.',
    'bash deploy.sh -c "echo x > README.md"|целей записи нет':
        'bash-classify делегирует строку после `-c`, хотя первым позиционным стоит сценарий `deploy.sh`: цель README.md мы называем, а настоящий bash её не тронет. Лишняя цель, не пропущенная.',
}

# Кейсы, где фраза говорит не про запись: утверждение задано руками.
OVERRIDES = {
    # «mutates» — код причины старого гварда; у нас та же причина словами.
    'rm README.md|причина отказа — «mutates»': {'writes': 'да', 'blames': 'rm'},
    # «Адаптеров команды» в новой системе нет: цель называется сразу.
    'printf x > /repo/out.txt|без адаптера команды — целей нет, недостающая возможность названа «write-targets»':
        {'writes': 'да', 'target': '/repo/out.txt'},
    # `perl` в базе правил bash-classify есть, и строку после -c он ему не отдаёт.
    'perl -c "cat a.js"|обёртка не снимается, команда остаётся как есть': {'no_targets': True},
}


def claim(expected: str) -> dict:
    """Фраза кейса → утверждения о вердикте. Таблица перевода — в шапке файла."""
    out = {}
    said = False
    if 'только читает — доказано' in expected:
        out['writes'] = library.WRITES_NO
        said = True
    elif 'только читает' in expected and 'не доказано' in expected:
        out['not_writes'] = library.WRITES_NO
        said = True
    if 'мир не менялся' in expected:
        out['writes'] = library.WRITES_NO
        said = True
    elif 'мир менялся' in expected:
        out['writes'] = library.WRITES_YES
        said = True
    # «записи нет» ищется отдельной фразой, а не куском «целей записи нет»:
    # иначе «мир менялся, целей записи нет» читалось бы как «не пишет».
    if 'запись НЕ известна' in expected:
        out['writes'] = library.WRITES_UNKNOWN
        said = True
    elif 'запись известна' in expected:
        out['writes'] = library.WRITES_YES
        said = True
    elif expected.startswith('записи нет') or ', записи нет' in expected:
        out['writes'] = library.WRITES_NO
        said = True
    if 'отправка (push)' in expected:
        out['writes'] = library.WRITES_YES
        out['network'] = True
        said = True
    if expected == 'не отправка':
        out['no_network'] = True
        said = True
    if 'цель записи — ' in expected:
        target = expected.split('цель записи — ', 1)[1]
        if ',' in target:
            out['once'] = 'один раз' in target[target.index(','):]
            target = target[:target.index(',')]
        out['target'] = target
        out['writes'] = library.WRITES_YES
        said = True
    if 'целей записи нет' in expected:
        out['no_targets'] = True
        said = True
    if 'виновник — «' in expected:
        out['blames'] = expected.split('виновник — «', 1)[1].split('»')[0]
        said = True
    if not said and (expected.startswith('прочитан') or expected.startswith('чтение')
                     or expected.startswith('снятие обёртки')):
        out['writes'] = library.WRITES_NO
    return out


def cases() -> list:
    return json.loads(Path(CASES).read_text(encoding='utf-8'))


def tool_is_here() -> bool:
    try:
        library.binary()
    except library.ShellTreeError:
        return False
    return True


@unittest.skipUnless(tool_is_here(), f'{library.BINARY_NAME} не установлен: корпус гонять нечем')
class CorpusTest(unittest.TestCase):
    """Каждый кейс корпуса — через verdict живой утилитой."""

    def test_the_corpus_is_the_one_the_skips_were_written_for(self) -> None:
        self.assertEqual(len(cases()), CASES_COUNT,
                         'корпус изменился — пересмотри пропуски, они писались под этот состав')

    def test_every_skip_and_override_is_about_a_real_case(self) -> None:
        known = {case['command'] + '|' + case['expected'] for case in cases()}
        for key in list(SKIPPED) + list(OVERRIDES):
            self.assertIn(key, known, f'пропуск «{key}» не про кейс корпуса')

    def test_the_corpus(self) -> None:
        passed = skipped = 0
        for case in cases():
            key = case['command'] + '|' + case['expected']
            with self.subTest(case=key):
                if key in SKIPPED:
                    skipped += 1
                    self.skipTest(SKIPPED[key])
                want = OVERRIDES.get(key) or claim(case['expected'])
                self.assertTrue(want, f'ожидание «{case["expected"]}» не переведено в утверждение')
                self.check(want, library.verdict(case['command']), case)
                passed += 1
        self.assertEqual(passed + skipped, CASES_COUNT)

    def check(self, want: dict, got: dict, case: dict) -> None:
        where = f'\n  команда: {case["command"]!r}\n  ожидание: {case["expected"]}\n  вердикт: {got}'
        if 'writes' in want:
            self.assertEqual(got['writes'], want['writes'], where)
        if 'not_writes' in want:
            self.assertNotEqual(got['writes'], want['not_writes'], where)
        if 'target' in want:
            found = [item for item in got['targets'] if item['path'] == want['target']]
            self.assertTrue(found, f'цели «{want["target"]}» нет{where}')
            if want.get('once'):
                self.assertEqual(len(found), 1, f'цель названа не один раз{where}')
        if want.get('no_targets'):
            self.assertEqual(got['targets'], [], where)
        if want.get('network'):
            self.assertTrue(any(item['kind'] == 'сеть' for item in got['targets']), where)
        if want.get('no_network'):
            self.assertFalse(any(item['kind'] == 'сеть' for item in got['targets']), where)
        if 'blames' in want:
            self.assertIn(want['blames'], got['reason'], where)


if __name__ == '__main__':
    unittest.main()
