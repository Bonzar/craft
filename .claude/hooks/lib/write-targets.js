// Что считать записью и куда она метит. Общий дом для гвардов, которые стоят на
// одних и тех же вызовах: план-гейт и гвард якоря сессии. Разъехавшиеся копии
// этих предикатов дали бы поверхность, где одно и то же место у одного гварда
// гейтится, а у другого нет, и заметно это стало бы только на живом прогоне.
import { isIgnored } from './git.js';

// Путь, правка которого системным изменением не является.
export function isEphemeral(fp) {
  // Файл плана пишет план-мод ДО того, как появится одобрение, — гейт на нём
  // заклинил бы само планирование.
  if (/\/plans\/.*\.md$/.test(fp)) return true;
  if (/^(\/tmp\/|\/private\/tmp\/|\/var\/folders\/|\/private\/var\/folders\/)/.test(fp)) return true;
  if (fp.includes('/scratchpad/')) return true;
  const tmp = process.env.TMPDIR;
  if (tmp && fp.startsWith(`${tmp.replace(/\/$/, '')}/`)) return true;
  // ~/.claude: харнесс непрерывно пишет туда служебное состояние (память,
  // сессии, задачи, тудушки) — оно обязано остаться свободным. Гейтятся только
  // СИСТЕМНЫЕ зоны: скиллы, хуки, агенты, правила, команды, воркфлоу, настройки.
  const home = process.env.HOME || '';
  if (home && fp.startsWith(`${home}/.claude/`)) {
    const rel = fp.slice(`${home}/.claude/`.length);
    const gated = /^(skills|hooks|agents|rules|commands|workflows)\//.test(rel)
      || ['settings.json', 'settings.local.json', 'craft.env'].includes(rel);
    return !gated;
  }
  return false;
}

// Игнорируемое гитом эфемерно (сборка, логи) для ЛЮБОГО инструмента записи,
// кроме путей внутри .claude/: там игнор не оправдание.
export function gitEphemeral(fp) {
  if (fp.startsWith('.claude/') || fp.includes('/.claude/')) return false;
  return isIgnored(fp);
}

// Тела heredoc с ЗАКАВЫЧЕННЫМ маркером вычёркиваются ПЕРВЫМИ, до снятия кавычек:
// после снятия маркер <<'PY' неотличим от << и опознать его нечем. Внутри такого
// тела shell-подстановок не бывает по определению, а «больше» там — сравнение кода
// (i>0:), не перенаправление; сама строка-открыватель остаётся в скане целиком,
// потому что перенаправление формы `cat <<'EOF' > файл` стоит именно на ней.
// Незакавыченный маркер не вычёркивается: в его теле живут подстановки.
function stripQuotedHeredocs(text) {
  const out = [];
  let mark = '';
  let inside = false;
  for (const line of text.split('\n')) {
    if (inside) {
      if (line === mark) inside = false;
      continue;
    }
    const found = line.match(/<<[ \t]*["'][A-Za-z_][A-Za-z0-9_]*["']/);
    if (found) {
      mark = found[0].replace(/^<<[ \t]*["']/, '').replace(/["']$/, '');
      inside = true;
    }
    out.push(line);
  }
  return out.join('\n');
}

// Все совпадения регулярки по строкам текста — как их печатал grep -oE.
function matchAll(text, re) {
  const found = [];
  for (const line of text.split('\n')) {
    for (const m of line.matchAll(new RegExp(re, 'g'))) found.push(m[0]);
  }
  return found;
}

const lastField = (s) => s.trim().split(/\s+/).pop();

// Цели записи Bash-команды: перенаправление (> >>), tee, правка на месте (-i),
// cp/mv (последний аргумент либо явная цель после -t), запись из интерпретатора
// (open(…,'w'), write_text/bytes). Гейтится цель, а не команда: сборка,
// копирование в игнорируемый путь и любое чтение целей не дают.
export function bashWriteTargets(cmd) {
  // Знак «больше» бывает и сравнением: в кавычках (jq 'select(.size > 10)') и в условных
  // скобках ([[ a > b ]]). Оба места вычёркиваются — но в ОТДЕЛЬНУЮ строку: разбору
  // записи из интерпретатора нужны буквальные кавычки вокруг пути, на вычеркнутой он бы
  // ослеп. Цена — перенаправление в закавыченную цель (> "мой файл") не увидится.
  const scan = stripQuotedHeredocs(cmd).split('\n').map((line) => line
    .replace(/'[^']*'/g, ' ')
    .replace(/"[^"]*"/g, ' ')
    .replace(/\[\[[^\]]*\]\]/g, ' ')
    .replace(/\(\([^)]*\)\)/g, ' ')).join('\n').replace(/\n+$/, '');

  const targets = [];
  for (const m of matchAll(scan, />>?[ \t]*[^|&;()<>\s]+/.source)) {
    targets.push(m.replace(/^>>?[ \t]*/, ''));
  }
  for (const m of matchAll(scan, /\btee\b([ \t]+-[a-zA-Z]+)*[ \t]+[^|&;()<>\s]+/.source)) {
    targets.push(lastField(m));
  }
  for (const m of matchAll(scan, /\b(sed|perl)\b[^|&;]*[ \t]-i[^|&;]*/.source)) {
    for (const word of m.split(' ')) if (/[/.]/.test(word)) targets.push(word);
  }
  for (const m of matchAll(scan, /\b(cp|mv)\b[^|&;]*[ \t]-t[ \t]+[^\s|&;]+/.source)) {
    targets.push(m.replace(/^.*[ \t]-t[ \t]+/, ''));
  }
  const noDashT = scan.split('\n').filter((line) => !/[ \t]-t[ \t]/.test(line)).join('\n');
  for (const m of matchAll(noDashT, /\b(cp|mv)\b[ \t]+[^|&;()<>]+/.source)) {
    targets.push(lastField(m));
  }
  for (const m of matchAll(cmd, /open\([ \t]*['"][^'"]+['"][ \t]*,[ \t]*['"][wa]/.source)) {
    targets.push(m.replace(/^open\([ \t]*['"]/, '').replace(/['"].*$/, ''));
  }
  for (const m of matchAll(cmd, /Path\([ \t]*['"][^'"]+['"][ \t]*\)[ \t]*\.[ \t]*write_(text|bytes)/.source)) {
    targets.push(m.replace(/^Path\([ \t]*['"]/, '').replace(/['"].*$/, ''));
  }
  return targets.map((t) => resolveAgainstCd(scan, t));
}

// Команда часто переходит в каталог и пишет уже относительным именем:
// «cd /tmp/work && cat > notes.md». Цель, взятая как написана, начинается не с
// /tmp — и запись во временный каталог гейтилась, хотя та же запись абсолютным
// путём проходила свободно.
//
// База берётся из АБСОЛЮТНОГО cd: относительный («cd ..») перевёл бы из
// каталога, которого разбор не знает, и склейка соврала бы. Последний cd в
// команде выигрывает — он и есть тот, в котором команда пишет.
function resolveAgainstCd(scan, target) {
  if (!target || target.startsWith('/') || target.startsWith('-')) return target;
  const dirs = matchAll(scan, /\bcd[ \t]+\/[^\s|&;()<>]*/.source)
    .map((m) => m.replace(/^cd[ \t]+/, '').replace(/\/$/, ''));
  if (!dirs.length) return target;
  return `${dirs[dirs.length - 1]}/${target.replace(/^\.\//, '')}`;
}

// Цель записи, очищенная от кавычек; дескрипторы и устройства целями не
// являются и отсеиваются здесь же — пустая строка означает «это не цель».
export function cleanTarget(rawTarget) {
  if (!/\S/.test(rawTarget)) return '';
  if (rawTarget.startsWith('/dev/') || rawTarget.startsWith('-')) return '';
  if (['0', '1', '2', '&1', '&2'].includes(rawTarget)) return '';
  return rawTarget.replace(/"$/, '').replace(/^"/, '').replace(/'$/, '').replace(/^'/, '');
}
