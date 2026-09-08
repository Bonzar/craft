// СВЕРКА ДВУХ СТОРОН на широком наборе форм: ручной разбор с базы 24392fb против
// дерева на голове. Один кейс вместо шестого круга ревью.
//
// ЗАЧЕМ ИМЕННО ТАК. Пять кругов ревью нашли шесть дыр, все одного класса: голова
// МОЛЧА пропускает запись, которую база отбивала. Круг ревью находил по ОДНОМУ
// пути за раз, а перебор форм — по critical за проход. Ручные ожидания вместо
// базовой стороны не годятся: писал бы их тот же глаз, что эти дыры и пропустил;
// поэтому эталон — замороженная копия базы в tests/fixtures/base-24392fb.
//
// ЧТО СВЕРЯЕТСЯ. Четыре вопроса, которые слой задаёт команде:
//   commandTargets   — что она пишет (спрашивает план-гейт и леджер);
//   classifyCommand  — доказано ли, что она только читает (гвард якоря);
//   commandReads     — что она прочитала (гвард «не правь того, чего не читал»);
//   gitMutates       — меняет ли она рабочую копию.
//
// АСИММЕТРИЯ, она же смысл кейса. Совпадения НЕ требуется: голова затем и
// писалась, чтобы отвечать точнее. Требуется отсутствие ПОТЕРЬ. Голова мягче
// базы хоть в одном вопросе — ПРОВАЛ; голова строже или отвечает иначе —
// расхождение, и каждое перечислено ниже поимённо с причиной.
//
// «Мягче» у каждого вопроса своё, потому что своя и цена ошибки:
//   цели ЗАПИСИ — база назвала, голова нет: запись идёт мимо гейта, и пустой
//     список от «команда ничего не пишет» гейту неотличим;
//   доказанное ЧТЕНИЕ — голова доказала, база нет: гвард якоря перестаёт держать;
//   цели ЧТЕНИЯ — голова назвала прочитанным то, чего база не называла: это
//     РАЗРЕШЕНИЕ править файл, поэтому такие идут в разбор глазами поимённо;
//   gitMutates — база сказала «меняет», голова нет.
//
// ПОКРЫТИЕ идёт по осям (пусковые префиксы, git, форма цели, вид подстановки,
// вложение, чтение), и каждая из шести найденных кругами дыр закрыта не одной
// формой, а соседями: одна форма ловит одну починку, а класс дефекта ловит ряд.
//
// СРОК ЖИЗНИ. Кейс и снимок уходят вместе с JS-половиной слоя: когда
// lib/write-targets*.js уедут в пакеты, сверять станет не с чем.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as head from '../../.claude/hooks/lib/write-targets-bash.js';
import * as headGit from '../../.claude/hooks/lib/write-targets-git.js';
import * as base from '../fixtures/base-24392fb/write-targets-bash.js';
import * as baseGit from '../fixtures/base-24392fb/write-targets-git.js';

// --- таблица форм -------------------------------------------------------------
//
// Ось «префикс»: имя команды записи стоит НЕ первым словом. Дыра круга 4 —
// поиск шёл только по первому слову, и десять форм уходили мимо гейта.
const PREFIX = [
  'tee /repo/out.txt',
  'sudo tee /repo/out.txt',
  'sudo -u deploy tee /repo/out.txt',
  'sudo --user=deploy tee /repo/out.txt',
  'doas tee /repo/out.txt',
  'env tee /repo/out.txt',
  'env FOO=1 tee /repo/out.txt',
  'env -i PATH=/usr/bin tee /repo/out.txt',
  'env -u HOME tee /repo/out.txt',
  'exec tee /repo/out.txt',
  'exec -a proxy tee /repo/out.txt',
  'nice tee /repo/out.txt',
  'nice -n 5 tee /repo/out.txt',
  'nohup tee /repo/out.txt',
  'setsid tee /repo/out.txt',
  'time tee /repo/out.txt',
  'command tee /repo/out.txt',
  'timeout 5 tee /repo/out.txt',
  'stdbuf -o0 tee /repo/out.txt',
  'ionice -c3 tee /repo/out.txt',
  'taskset -c 0 tee /repo/out.txt',
  'xargs tee /repo/out.txt',
  'echo a | xargs tee /repo/out.txt',
  'echo a | xargs -0 tee /repo/out.txt',
  'xargs -I{} cp {} /repo/out.txt',
  'find . -name "*.js" -exec tee /repo/out.txt \\;',
  'find . -type f -exec cp {} /repo/out.txt \\;',
  'find /repo -name "*.js" -exec sed -i s/a/b/ {} +',
  "su -c 'tee /repo/out.txt'",
  "su deploy -c 'tee /repo/out.txt'",
  '/usr/bin/tee /repo/out.txt',
  '/bin/cp a.js /repo/out.txt',
  'sudo /usr/bin/tee /repo/out.txt',
  'nice -n 5 sudo tee /repo/out.txt',
  'time sudo tee /repo/out.txt',
  'sudo tee -a /repo/out.txt',
  'sudo dd if=a.js of=/repo/out.txt',
  'sudo sed -i s/a/b/ /repo/out.txt',
];

// Ось «git названный путём»: дыра круга 2. Обход инструмента через путь.
const GIT = [
  'git commit -m x',
  'git commit --no-verify -m x',
  'git push',
  'git push --force',
  'git status',
  'git log --oneline',
  'git diff',
  '/usr/bin/git commit -m x',
  '/usr/local/bin/git push --force',
  'sudo /usr/bin/git reset --hard HEAD~1',
  'env git commit -m x',
  'git -C /repo commit -m x',
  'git add .',
  'git checkout -- .',
  'git clean -fd',
  'git stash',
  'git rebase main',
  'git merge main',
  'git apply patch.diff',
  'git commit -am x',
];

// Ось «форма цели»: перенаправления всех видов, операнды, heredoc, кавычки,
// пробелы и не-латиница в имени.
const TARGET = [
  'echo x > /repo/out.txt',
  'echo x >> /repo/out.txt',
  'echo x >| /repo/out.txt',
  'echo x &> /repo/out.txt',
  'echo x &>> /repo/out.txt',
  'echo x 1> /repo/out.txt',
  'echo x 2> /repo/err.txt',
  'echo x 2>> /repo/err.txt',
  'echo x > /repo/out.txt 2>&1',
  'echo x 2>&1',
  'echo x >&2',
  'cat <> /repo/out.txt',
  'echo x > /dev/null',
  'cat a.js > /tmp/out.txt',
  'exec > /repo/out.txt',
  'exec 3> /repo/out.txt',
  'echo x 3> /repo/out.txt',
  'cat > /repo/out.txt <<EOF\nтело\nEOF',
  "cat > /repo/out.txt <<'EOF'\nтело\nEOF",
  'cat >> /repo/out.txt <<EOF\nтело\nEOF',
  'echo x > "/repo/мой файл.md"',
  "echo x > '/repo/мой файл.md'",
  'echo x > /repo/мой-файл.md',
  'echo x > "/repo/out.txt"',
  "echo x > '/repo/out.txt'",
  'echo x > out.txt',
  'echo x > ./out.txt',
  'echo x > ../out.txt',
  'echo x > /repo/../out.txt',
  'printf "x" > /repo/out.txt',
  'cat a.js > /repo/out.txt 2> /repo/err.txt',
  'tee /repo/notes.md',
  'tee -a /repo/out.txt',
  'mv a.js /repo/out.txt',
  'cp a.js /repo/out.txt',
  'cp -r src /repo/dst',
  'dd if=a.js of=/repo/out.txt',
  'install -m 0644 a.js /repo/out.txt',
  'ln -s a.js /repo/out.txt',
  'ln -sf a.js /repo/out.txt',
  'sed -i s/a/b/ /repo/out.txt',
  'sed --in-place s/a/b/ /repo/out.txt',
  'sed -i.bak s/a/b/ /repo/out.txt',
  'perl -i -pe s/a/b/ /repo/out.txt',
  'truncate -s 0 /repo/out.txt',
  'touch /repo/out.txt',
  'rm /repo/out.txt',
  'rm -rf /repo/dir',
  'mkdir -p /repo/dir',
  'chmod 644 /repo/out.txt',
  'tar -xzf a.tgz -C /repo',
  'unzip a.zip -d /repo',
  'curl -o /repo/out.txt https://example.invalid/a',
  'wget -O /repo/out.txt https://example.invalid/a',
  'python3 -c "open(\'/repo/out.txt\',\'w\')"',
  'sudo tee "/repo/мой файл.md"',
];

// Ось «подстановка»: дыры кругов 1, 2, 3 и 5. Цель выдумывалась, цель из одной
// подстановки исчезала, цель-операнд с подстановкой исчезала, а команда внутри
// раскрытия пропадала из дерева ВОВСЕ.
const SUBST = [
  'cat > $OUT',
  'cat a > $OUT',
  'cat > ${OUT}',
  'cat a > "$OUT"',
  'cat > $HOME/notes.md',
  'cat > ${HOME}/notes.md',
  'cat > "$HOME/notes.md"',
  'cat > $PWD/notes.md',
  'cat > ~/notes.md',
  'cat > $(dirname /repo/out.txt)/out.txt',
  'cat > `dirname /repo`/out.txt',
  'cat > ${OUT:-/repo/out.txt}',
  'cat > ${OUT:-$(echo /repo/out.txt)}',
  'tee $OUT',
  'tee ${OUT}',
  'tee "$OUT"',
  'mv a.js $DEST',
  'cp a.js ${DEST}',
  'dd if=a.js of=$OUT',
  'install -m 0644 a.js $DEST',
  'ln -s a.js $DEST',
  'sed -i s/a/b/ $F',
  'sudo tee $OUT',
  'x=$(tee /repo/out.txt)',
  'x=`tee /repo/out.txt`',
  'echo $(tee /repo/out.txt)',
  'echo $(git commit -m x)',
  'echo $(rm -rf /repo/docs)',
  'echo `rm -rf /repo/docs`',
  'echo ${x:-$(rm -rf /repo/docs)}',
  'echo ${x/a/$(rm -rf /repo/docs)}',
  'echo ${x#$(rm -rf /repo/docs)}',
  'arr=($(rm -rf /repo/docs))',
  'let n=$(rm -rf /repo/docs)',
  'echo $(( $(rm -rf /repo/docs) ))',
  '[[ -n $(rm -rf /repo/docs) ]]',
  'echo ${x:-$(tee /repo/out.txt)}',
  'arr=($(tee /repo/out.txt))',
];

// Ось «вложение»: обёртка запуска, подоболочка, фигурные скобки, пайпы, && и ||,
// перевод строки, и cd внутри каждого. Дыра Codex — cd в фигурных скобках.
const NEST = [
  'bash -c "tee /repo/out.txt"',
  "bash -c 'tee /repo/out.txt'",
  'sh -c "tee /repo/out.txt"',
  'bash -lc "tee /repo/out.txt"',
  'bash -c "bash -c \\"tee /repo/out.txt\\""',
  'bash -c \'bash -c "cat > /repo/deep.md"\'',
  'sudo bash -c \'bash -c "tee /repo/deep.md"\'',
  'sudo bash -c "tee /repo/out.txt"',
  'env FOO=1 bash -c "tee /repo/out.txt"',
  'bash -c "cd /repo && tee out.txt"',
  'bash -c "echo x > $OUT"',
  'bash -c "sudo tee /repo/out.txt"',
  '( tee /repo/out.txt )',
  '( cd /repo && tee out.txt )',
  '( sudo tee /repo/out.txt )',
  '( cd /repo && ( cd docs && echo x > f ) )',
  '{ tee /repo/out.txt; }',
  '{ cd /repo; tee out.txt; }',
  '{ sudo tee /repo/out.txt; }',
  'cd /tmp; { cd /repo; echo x > f; }',
  'cd /tmp && cd /repo && echo x > f',
  'cd /repo && echo x > out.txt',
  'cd /repo || echo x > out.txt',
  'cd /repo\necho x > out.txt',
  'cd /repo; echo x > out.txt',
  'cd /repo && sudo tee out.txt',
  'cd /repo && bash -c "tee out.txt"',
  'cd /repo && echo x > "мой файл.md"',
  'cd /repo && cat > out.txt <<EOF\nтело\nEOF',
  'cd /tmp | cat $(echo x) a.js',
  'echo a | tee /repo/out.txt',
  'echo a | sudo tee /repo/out.txt',
  'echo a | tee -a /repo/out.txt | wc -l',
  'cat a.js && tee /repo/out.txt',
  'cat a.js || tee /repo/out.txt',
  'cat a.js\ntee /repo/out.txt',
  'for f in a b; do tee /repo/$f.txt; done',
  'while read l; do tee /repo/out.txt; done',
  'if true; then tee /repo/out.txt; fi',
  'case x in a) tee /repo/out.txt ;; esac',
  'f() { tee /repo/out.txt; }',
  'tee /repo/out.txt &',
];

// Ось «чтение»: доказанное чтение и цели чтения. Здесь асимметрия ОБРАТНАЯ —
// лишняя цель чтения открывает правку файла, которого никто не читал.
const READ = [
  'cat a.js',
  'cat /repo/a.js',
  'cat "мой файл.md"',
  "cat 'мой файл.md'",
  'cat a.js b.js',
  '/bin/cat a.js',
  '/usr/bin/grep -rn образец lib',
  'head -20 a.js',
  'tail -n 5 a.js',
  'grep -rn образец lib',
  'grep -rn образец /repo/lib',
  'ls -la',
  'ls -la /repo',
  'cd /repo && cat a.js',
  'cd /repo && ls',
  'cat a.js | grep x',
  'cat $FILE',
  'cat ${FILE}',
  'cat "$FILE"',
  'cat $(echo a.js)',
  'diff <(echo a) b.txt',
  'diff <(cat a.js) <(cat b.js)',
  'sed -n 1,10p a.js',
  'awk "NR<10" a.js',
  'wc -l a.js',
  'file a.js',
  'stat a.js',
  'echo hi',
  'true',
  'sleep 1',
  'python3 -c "print(1)"',
  'python3 script.py',
  'node -e "console.log(1)"',
  'bash -c "cat a.js"',
  'bash -c "rm -rf /repo"',
  'cat a.js; rm /repo/b.js',
  'cat a.js && rm /repo/b.js',
];

const FORMS = [
  ...PREFIX.map((f) => ['префикс', f]),
  ...GIT.map((f) => ['git', f]),
  ...TARGET.map((f) => ['цель', f]),
  ...SUBST.map((f) => ['подстановка', f]),
  ...NEST.map((f) => ['вложение', f]),
  ...READ.map((f) => ['чтение', f]),
];

// --- сравнение ----------------------------------------------------------------
//
// Метка нераскрытого у сторон разная по написанию: база обёртывает слово нулевыми
// байтами, голова — пробелами. Поэтому узнаётся по СЛОВУ, а не по константе, а
// сами обёртки снимаются как всё непечатное.
const MARK = 'нераскрыто';
const marked = (t) => String(t).includes(MARK);
const printable = (s) => [...s].filter((c) => c.codePointAt(0) > 31).join('');
// Хвостовая пунктуация — след ЧУЖОГО разбора, а не часть имени файла: ручной
// разбор оставлял на цели закрывающую обратную кавычку (`x=`tee f``) и косую от
// терминатора перебора. Сравнивать по ней значит расходиться там, где стороны
// назвали один и тот же файл.
const bare = (t) => printable(String(t).split(MARK).join('')).trim().replace(/[`\\'"]+$/, '');
const tail = (t) => t.split('/').filter(Boolean).pop() || '';

// Покрыта ли цель одной стороны ответом другой. Клаузы названы поимённо, потому
// что каждая — сознательная слепота этого кейса:
//   — цель нераскрыта или у другой стороны есть нераскрытая: такая команда
//     гейтится ЦЕЛИКОМ, мимо гейта не проходит ничего, и точнее назвать никто
//     не брался;
//   — от цели после чистки не осталось ничего: это мусор разбора (одна косая от
//     `-exec … \;`), и терять там нечего;
//   — та же строка: ответы совпали;
//   — цель одной стороны — НАЧАЛО цели другой до пробела: ручной разбор резал
//     закавыченное имя по первому пробелу (`"/repo/мой файл.md"` → `/repo/мой`),
//     и это его дефект, а не потеря у дерева;
//   — совпал ХВОСТ пути: то же слово стало целью, разошёлся резолв КАТАЛОГА, а
//     это отдельный вопрос со своими кейсами (и голова там, где стороны
//     расходятся, отвечает вернее — находка Codex про фигурные скобки).
function covered(x, them) {
  const a = bare(x);
  if (marked(x) || a === '' || them.some(marked)) return true;
  return them.some((y) => {
    const b = bare(y);
    return b === a || b.startsWith(`${a} `) || a.startsWith(`${b} `) || tail(b) === tail(a);
  });
}

function compare(kind, form) {
  const out = [];
  const bt = base.commandTargets(form);
  const ht = head.commandTargets(form);
  if (bt.length && !ht.length) {
    out.push({ level: 'провал', what: `цели записи пропали целиком: база ${JSON.stringify(bt)}` });
  } else {
    const lost = bt.filter((x) => !covered(x, ht));
    if (lost.length) out.push({ level: 'провал', what: `цель записи потеряна: ${JSON.stringify(lost)}` });
  }
  const gained = ht.filter((x) => !covered(x, bt));
  if (gained.length) out.push({ level: 'строже', what: `цели записи добавились: ${JSON.stringify(gained)}` });

  const bc = base.classifyCommand(form);
  const hc = head.classifyCommand(form);
  if (hc.readOnly === true && bc.readOnly !== true) {
    out.push({ level: 'провал', what: `голова доказала чтение там, где база не доказала (${bc.cause})` });
  } else if (bc.readOnly === true && hc.readOnly !== true) {
    out.push({ level: 'строже', what: `голова не доказала чтение (${hc.cause}), база доказывала` });
  } else if (bc.cause !== hc.cause) {
    out.push({ level: 'иначе', what: `причина сменилась: ${bc.cause} -> ${hc.cause}` });
  }

  const br = base.commandReads(form);
  const hr = head.commandReads(form);
  const opened = hr.targets.filter((x) => !covered(x, br.targets));
  if (opened.length) out.push({ level: 'иначе', what: `цели чтения добавились: ${JSON.stringify(opened)}` });
  const dropped = br.targets.filter((x) => !covered(x, hr.targets));
  if (dropped.length) out.push({ level: 'строже', what: `цели чтения убыли: ${JSON.stringify(dropped)}` });
  if (br.mutates !== hr.mutates) out.push({ level: 'иначе', what: `mutates: ${br.mutates} -> ${hr.mutates}` });

  const bg = baseGit.gitMutates(form);
  const hg = headGit.gitMutates(form);
  if (bg && !hg) out.push({ level: 'провал', what: 'gitMutates: база да, голова нет' });
  if (!bg && hg) out.push({ level: 'строже', what: 'gitMutates: голова да, база нет' });

  return out.map((o) => ({ ...o, kind, form }));
}

// --- расхождения, разобранные глазами -----------------------------------------
//
// Ключ — форма, значение — одна строка «почему». Список ЗАКРЫТ: новое
// расхождение роняет кейс, потому что незамеченное расхождение и есть тот способ,
// каким шесть дыр подряд доезжали до головы.
const DIVERGENCES = new Map([
  // Голова видит цель, которой ручной разбор не находил.
  ['find . -type f -exec cp {} /repo/out.txt \\;', 'база давала косую от терминатора, голова — саму цель'],
  ['echo x >| /repo/out.txt', 'перенаправление с отменой noclobber ручному разбору известно не было'],
  ['sed --in-place s/a/b/ /repo/out.txt', 'длинный ключ правки на месте ручной разбор не узнавал'],
  // Ручной разбор был здесь НЕПОСЛЕДОВАТЕЛЕН: ту же двойную обёртку с
  // экранированными кавычками он цель называл (искал по тексту), а эту, с
  // одинарными снаружи, терял. Дерево разбирает обе одинаково.
  ['bash -c \'bash -c "cat > /repo/deep.md"\'', 'вторая обёртка: база теряла эту форму, находя соседнюю'],

  // Голова разбирает конструкцию оболочки и потому УСТАНАВЛИВАЕТ запись там, где
  // ручной разбор только не доказывал чтения. Для гейта это одно и то же (обе
  // стороны не пропускают), для леджера — установленный факт вместо неизвестности.
  ['cat > ${OUT:-$(echo /repo/out.txt)}', 'база строку не разобрала вовсе, голова разобрала'],
  ['echo $(git commit -m x)', 'вызов внутри подстановки: голова видит утверждение, база — текст'],
  ['bash -lc "tee /repo/out.txt"', 'кластер ключей у обёртки: голова спускается в тело'],
  ['{ tee /repo/out.txt; }', 'содержимое фигурных скобок голова разбирает'],
  ['{ cd /repo; tee out.txt; }', 'то же, вместе с переходом каталога внутри'],
  ['cd /tmp; { cd /repo; echo x > f; }', 'переход в скобках: голова даёт /repo/f, база давала /tmp/f (находка Codex)'],
  ['cd /repo && bash -c "tee out.txt"', 'спуск в тело обёртки с резолвом каталога'],
  ['for f in a b; do tee /repo/$f.txt; done', 'тело цикла голова разбирает'],
  ['case x in a) tee /repo/out.txt ;; esac', 'ветку выбора голова разбирает'],
  ['f() { tee /repo/out.txt; }', 'тело объявляемой функции голова разбирает'],

  // ЕДИНСТВЕННОЕ место, где голова знает МЕНЬШЕ базы, и потому оно здесь названо
  // отдельно. Тело обёртки приходит словом, из которого подстановка уже стёрта
  // (`echo x > `), и разбор честно отвечает синтаксической ошибкой; ручной поиск
  // по тексту видел там образец записи и звал команду меняющей.
  //
  // Гейт от этого не слабеет: целей у формы нет НИ У ОДНОЙ стороны, и план-гейт
  // на обеих выходит одинаково. Расходится только запись в леджер: у базы это
  // «менял, цели не назвал», у головы — «не разобрал». Обе названы вслух, и
  // молчаливого «события мира не было» нет ни там, ни там.
  ['bash -c "echo x > $OUT"', 'подстановка в теле обёртки: база гадала по тексту, голова говорит «не разобрал»'],
]);

test('сверка двух сторон: голова нигде не мягче базы', () => {
  const failures = [];
  const divergences = [];
  for (const [kind, form] of FORMS) {
    for (const note of compare(kind, form)) {
      (note.level === 'провал' ? failures : divergences).push(note);
    }
  }
  const shown = failures.map((f) => `[${f.kind}] ${JSON.stringify(f.form)} — ${f.what}`);
  assert.deepEqual(shown, [], `потери поведения:\n${shown.join('\n')}`);

  const unexplained = [...new Set(divergences.map((d) => d.form))].filter((f) => !DIVERGENCES.has(f));
  const detail = unexplained.map((f) => {
    const notes = divergences.filter((d) => d.form === f).map((d) => `${d.level}: ${d.what}`);
    return `  ${JSON.stringify(f)}\n    ${notes.join('\n    ')}`;
  });
  assert.deepEqual(detail, [], `расхождения без объяснения:\n${detail.join('\n')}`);

  const stale = [...DIVERGENCES.keys()].filter((f) => !divergences.some((d) => d.form === f));
  assert.deepEqual(stale, [], `объяснения без расхождения (список протух): ${JSON.stringify(stale)}`);
});

test('таблица форм разложена по осям и покрывает найденные дыры', () => {
  const byKind = new Map();
  for (const [kind] of FORMS) byKind.set(kind, (byKind.get(kind) || 0) + 1);
  for (const [kind, n] of byKind) assert.ok(n >= 20, `ось ${kind}: форм ${n}, мало`);
  assert.ok(FORMS.length >= 200, `форм всего ${FORMS.length}, мало`);
  assert.equal(new Set(FORMS.map(([, f]) => f)).size, FORMS.length, 'в таблице есть повторы');
});
