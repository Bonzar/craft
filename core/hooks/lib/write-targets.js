// Что считать записью и куда она метит. Общий дом для всех проверок записи, которые
// стоят на одних и тех же вызовах: план-гейт и read-only-классификатор. Разъехавшиеся
// копии этих предикатов давали бы поверхность, где одно и то же место у одного гейта
// гейтается, а у другого — нет, и заметно это стало бы только на живом прогоне.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { isIgnored } from './git.js';
import { homeZoneState, isSystemPath } from './layout.js';
import parse from './vendor/shell-quote/parse.js';

// Путь, правка которого системным изменением не является.
export function isEphemeral(fp) {
  fp = normalizeTarget(fp);
  if (!lexicallyEphemeral(fp)) return false;
  const physical = physicalTarget(fp);
  return Boolean(physical) && lexicallyEphemeral(physical);
}

function lexicallyEphemeral(fp) {
  if (/^(\/tmp\/|\/private\/tmp\/|\/var\/folders\/|\/private\/var\/folders\/)/.test(fp)) return true;
  if (fp.includes('/scratchpad/')) return true;
  const tmp = process.env.TMPDIR;
  if (tmp && fp.startsWith(`${tmp.replace(/\/$/, '')}/`)) return true;
  // Харнесс непрерывно пишет в свой дом служебное состояние. Оно остаётся
  // свободным; системные каталоги и настройки перечисляет внешний layout.
  const home = process.env.HOME || '';
  const homes = home ? [...new Set([normalizeTarget(home), physicalTarget(home)].filter(Boolean))] : [];
  for (const candidate of homes) {
    const state = homeZoneState(fp, candidate);
    if (state.matched) return !state.gated;
  }
  return false;
}

// Игнорируемое гитом эфемерно (сборка, логи) для ЛЮБОГО инструмента записи,
// кроме путей системных адаптеров: там игнор не оправдание.
export function gitEphemeral(fp) {
  const physical = physicalTarget(normalizeTarget(fp));
  if (!physical || isSystemPath(physical) || isSensitiveLocalConfig(physical)) return false;
  return isIgnored(physical);
}

const SENSITIVE_POLICY = path.join(path.dirname(fileURLToPath(import.meta.url)), 'sensitive-local-files.json');
let sensitivePatterns;

function loadSensitivePatterns() {
  if (sensitivePatterns !== undefined) return sensitivePatterns;
  try {
    const value = JSON.parse(fs.readFileSync(SENSITIVE_POLICY, 'utf8'));
    sensitivePatterns = {
      basename: value.basenamePatterns.map((pattern) => new RegExp(pattern, 'i')),
      segment: value.pathSegmentPatterns.map((pattern) => new RegExp(pattern, 'i')),
    };
  } catch {
    // A missing or invalid policy must never turn ignored credentials into an
    // ephemeral exemption. null means conservatively treat every ignored file
    // as sensitive until the policy is repaired.
    sensitivePatterns = null;
  }
  return sensitivePatterns;
}

function isSensitiveLocalConfig(fp) {
  const policy = loadSensitivePatterns();
  if (!policy) return true;
  const basename = path.basename(fp);
  const segments = normalizeTarget(fp).split('/').filter(Boolean);
  return policy.basename.some((pattern) => pattern.test(basename))
    || segments.some((segment) => policy.segment.some((pattern) => pattern.test(segment)));
}

// Resolve every existing ancestor, not just the leaf. A non-existent file
// below /tmp may still traverse an existing symlink into a repository.
function physicalTarget(fp) {
  if (!fp) return '';
  let absolute = normalizeTarget(fp.startsWith('/') ? fp : path.resolve(process.cwd(), fp));
  // realpath(3) fails on a dangling link and thereby loses the link target.
  // Walk components with lstat/readlink so a not-yet-created destination on the
  // other side of a temporary symlink is still classified by its real address.
  for (let links = 0; links < 64; links += 1) {
    const parts = absolute.split('/').filter(Boolean);
    let resolved = '/';
    let restarted = false;
    for (let i = 0; i < parts.length; i += 1) {
      const candidate = path.join(resolved, parts[i]);
      let stat;
      try {
        stat = fs.lstatSync(candidate);
      } catch (error) {
        if (['ENOENT', 'ENOTDIR'].includes(error?.code)) {
          return normalizeTarget(path.join(resolved, ...parts.slice(i)));
        }
        return '';
      }
      if (stat.isSymbolicLink()) {
        let link;
        try {
          link = fs.readlinkSync(candidate);
        } catch {
          return '';
        }
        absolute = normalizeTarget(path.resolve(path.dirname(candidate), link, ...parts.slice(i + 1)));
        restarted = true;
        break;
      }
      resolved = candidate;
    }
    if (!restarted) return normalizeTarget(resolved);
  }
  return '';
}

// Files addressed by a canonical multi-file patch. Every file guard
// consumes this parser instead of maintaining its own partial regex.
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

// Обезвредить закавыченное во ВСЕЙ команде разом, а не построчно.
//
// Содержимое кавычек СОХРАНЯЕТСЯ, меняются только служебные символы внутри них.
// Прежний разбор выбрасывал закавыченное целиком — и `cat > "/repo/README.md"`
// не давал ни одной цели: правка репозитория проходила гейт молча, потому что
// гейтить было нечего. Обратная беда там же: `jq \'select(.size > 10)\'` давала
// ложную цель, если кавычки не учесть вовсе.
//
// Замена одного символа на другой держит оба конца: путь внутри кавычек остаётся
// целым словом и виден как цель, а «больше», труба и точка с запятой внутри
// кавычек перестают выглядеть перенаправлением и разделителем.
//
// Ещё две беды жили в прежних двух заменах регуляркой — сперва одинарные
// кавычки, потом двойные. Вложенные: одинарные внутри двойных съедались первыми,
// двойные оставались непарными, и в остатке всплывал знак «больше». И строка в
// кавычках, охватывающая перевод строки: многострочная `node -e "…"` разбиралась
// по строкам, со второй строки разбор считал себя вне кавычек, и стрелка `=>` в
// JS-коде читалась как запись в файл.
//
// Переводы строк сохраняются: дальнейшие регулярки работают построчно, и склейка
// строк дала бы им чужие соседства.
//
// Кавычка внутри чужих кавычек — обычный символ, а не открывающая: ровно так её
// читает и сам шелл.
const NEUTRAL = '~';

function stripQuoted(text) {
  let out = '';
  let quote = '';
  for (const ch of text) {
    if (ch === '\n') {
      out += ch;
      continue;
    }
    if (quote) {
      if (ch === quote) {
        quote = '';
        continue;
      }
      out += '<>|&;()'.includes(ch) ? NEUTRAL : ch;
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      continue;
    }
    out += ch;
  }
  return out;
}

// Цели записи командного действия: перенаправление (> >>), tee, правка на месте (-i),
// cp/mv (последний аргумент либо явная цель после -t), запись из интерпретатора
// (open(…,'w'), write_text/bytes). Гейтится цель, а не команда: сборка,
// копирование в игнорируемый путь и любое чтение целей не дают.
export function commandWriteTargets(cmd) {
  const targets = [];
  const plannedLinks = [];
  eachCommandPiece(cmd, (piece, cwd) => {
    for (const target of pieceTargets(piece)) {
      const resolved = resolveTarget(cwd, target);
      targets.push(resolvePlannedLink(resolved, plannedLinks));
    }
    const link = plannedSymbolicLink(piece, cwd);
    if (link) plannedLinks.push(link);
  });
  return targets.concat(interpreterTargets(cmd));
}

// Shell pieces execute from left to right. A symlink created in one piece can
// therefore redirect a later, lexically temporary write into a permanent tree
// even though the link does not exist while the gate is inspecting the command:
// `ln -s /repo /tmp/link; echo x > /tmp/link/README.md`.
//
// Keep this command-local alias map separate from physicalTarget(): the latter
// proves links that already exist on disk, while this map proves links that the
// command itself is about to create. The symlink source remains a reference,
// not a mutation target, so a standalone `ln -s /repo /tmp/link` still has only
// the temporary destination as its side effect.
function plannedSymbolicLink(piece, cwd) {
  let tokens;
  try {
    tokens = parse(piece);
  } catch {
    return null;
  }
  const argv = tokens.filter((token) => typeof token === 'string');
  let at = 0;
  while (argv[at] && (argv[at] === 'sudo' || /^[A-Za-z_][A-Za-z0-9_]*=/.test(argv[at]))) at += 1;
  if (String(argv[at] || '').replace(/^.*\//, '') !== 'ln') return null;

  const args = argv.slice(at + 1);
  const symbolic = args.some((word) => word === '--symbolic'
    || (/^-[^-][A-Za-z]*$/.test(word) && word.includes('s')));
  if (!symbolic || args.some((word) => word === '-t'
    || word === '--target-directory'
    || word.startsWith('--target-directory='))) return null;

  const operands = args.filter((word) => !word.startsWith('-'));
  if (operands.length !== 2) return null;
  const destination = resolveTarget(cwd, operands[1]);
  if (!destination?.startsWith('/')) return null;
  const source = operands[0].startsWith('/')
    ? normalizePath(operands[0])
    : normalizePath(`${path.posix.dirname(destination)}/${operands[0]}`);
  return { destination: normalizePath(destination), source };
}

function resolvePlannedLink(target, links) {
  if (!target?.startsWith('/')) return target;
  let resolved = normalizePath(target);
  for (let depth = 0; depth < 64; depth += 1) {
    const link = links
      .filter(({ destination }) => resolved === destination || resolved.startsWith(`${destination}/`))
      .sort((left, right) => right.destination.length - left.destination.length)[0];
    if (!link) return resolved;
    const suffix = resolved.slice(link.destination.length);
    const next = normalizePath(`${link.source}${suffix}`);
    if (next === resolved) return resolved;
    resolved = next;
  }
  return resolved;
}

// Run a visitor over shell pieces with the absolute working directory active at
// that point. Mutation detection and redirect detection must resolve relative
// paths identically or the two classifiers create different gate surfaces.
function eachCommandPiece(cmd, visit) {
  // Знак «больше» бывает и сравнением: в кавычках (jq 'select(.size > 10)') и в условных
  // скобках ([[ a > b ]]). Оба места вычёркиваются — но в ОТДЕЛЬНУЮ строку: разбору
  // записи из интерпретатора нужны буквальные кавычки вокруг пути, на вычеркнутой он бы
  // ослеп. Цена — перенаправление в закавыченную цель (> "мой файл") не увидится.
  const scan = stripQuoted(stripQuotedHeredocs(cmd)).split('\n').map((line) => line
    .replace(/\[\[[^\]]*\]\]/g, ' ')
    .replace(/\(\([^)]*\)\)/g, ' ')).join('\n').replace(/\n+$/, '');

  // Команда идёт КУСКАМИ слева направо, и каждый резолвится тем каталогом,
  // который действует В ЭТОМ МЕСТЕ. Брать последний cd на всю команду нельзя:
  // «cat > README.md && cd /tmp» пишет в текущий каталог, а не во временный, и
  // общий cd выдавал бы запись в репозиторий за эфемерную.
  let cwd = '';
  for (const piece of scan.split(/(?:\|\||&&|[;|\n])/)) {
    visit(piece, cwd);
    const cd = /(?:^|[ \t])cd[ \t]+(\/[^\s|&;()<>]*)/.exec(piece);
    if (cd) cwd = cd[1].replace(/\/$/, '');
  }
}

const TREE_MUTATORS = new Set(['rm', 'rmdir', 'mkdir', 'touch', 'truncate', 'shred', 'unlink', 'mv']);
const MODE_MUTATORS = new Set(['chmod', 'chown', 'chgrp']);
const DESTINATION_MUTATORS = new Set(['ln', 'install']);
const GIT_MUTATORS = new Set(['checkout', 'restore', 'clean', 'reset', 'stash', 'rm', 'mv']);
const VALUE_FLAGS = new Set(['-s', '--size', '-m', '--mode', '-t', '--target-directory']);

// Commands that mutate the working tree without redirects. Their targets feed
// the same ephemeral/permanent decision as redirects. Unknown or targetless
// mutations stay explicitly unbounded and therefore fail closed.
export function commandMutationTargets(cmd) {
  const targets = [];
  let unknownMutation = false;
  let unsafeSyntax = shellCanShadowCommands(cmd);
  let boundedMutationCount = 0;
  let pieces = [];
  try {
    pieces = tokenPieces(String(cmd || ''));
  } catch {
    return { targets, unknownMutation: true, unsafeSyntax: true, boundedMutationCount };
  }
  let cwd = '';
  for (const piece of pieces) {
    const mutation = mutationInTokens(piece);
    // Redirects bound only their own file write. They cannot serve as evidence
    // that a mutating executable in the same simple command is target-bounded.
    let bounded = boundedWriteInTokens(piece);
    if (mutation) {
      if (mutation.unsafe) unsafeSyntax = true;
      if (!mutation.targets.length) unknownMutation = true;
      if (mutation.targets.length) bounded = true;
      for (const target of mutation.targets) targets.push(resolveTarget(cwd, target));
    }
    if (bounded) boundedMutationCount += 1;
    const words = piece.filter((token) => typeof token === 'string');
    if (words[0] === 'cd' && words[1]?.startsWith('/')) cwd = normalizeTarget(words[1]);
  }
  return { targets, unknownMutation, unsafeSyntax, boundedMutationCount };
}

function boundedWriteInTokens(tokens) {
  const argv = tokens.filter((token) => typeof token === 'string');
  let at = 0;
  while (argv[at] && (argv[at] === 'sudo' || /^[A-Za-z_][A-Za-z0-9_]*=/.test(argv[at]))) at += 1;
  const name = String(argv[at] || '').replace(/^.*\//, '');
  const args = argv.slice(at + 1);
  if (name === 'tee') return pathArguments(args).length > 0;
  if (name === 'cp') return destinationTargets(args).length > 0;
  return false;
}

function shellCanShadowCommands(command) {
  const text = String(command || '');
  return /(?:^|[;&|\n]\s*)[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)\s*\{/.test(text)
    || /(?:^|[;&|\n]\s*)alias(?:\s|$)/.test(text)
    || /(?:^|[\s;&|])PATH\s*=/.test(text);
}

const CHAIN_OPS = new Set(['&&', '||', ';', '|', '&', '|&']);

function tokenPieces(command) {
  const pieces = [];
  let current = [];
  for (const token of parse(command)) {
    if (token && typeof token === 'object' && CHAIN_OPS.has(token.op)) {
      if (current.length) pieces.push(current);
      current = [];
      continue;
    }
    current.push(token);
  }
  if (current.length) pieces.push(current);
  return pieces;
}

function mutationInTokens(tokens) {
  const argv = tokens.filter((token) => typeof token === 'string');
  let at = 0;
  while (argv[at] && (argv[at] === 'sudo' || /^[A-Za-z_][A-Za-z0-9_]*=/.test(argv[at]))) at += 1;
  const rawName = String(argv[at] || '');
  const name = rawName.replace(/^.*\//, '');
  if (!name) return null;
  const knownMutation = TREE_MUTATORS.has(name)
    || MODE_MUTATORS.has(name)
    || DESTINATION_MUTATORS.has(name)
    || name === 'dd'
    || name === 'git';
  if (knownMutation && rawName.includes('/') && !/^\/(?:usr\/)?bin\/[A-Za-z0-9._+-]+$/.test(rawName)) {
    return { targets: [], unsafe: true };
  }

  if (TREE_MUTATORS.has(name)) {
    return { targets: pathArguments(argv.slice(at + 1)) };
  }
  if (MODE_MUTATORS.has(name)) {
    return { targets: pathArguments(argv.slice(at + 1)).slice(1) };
  }
  if (DESTINATION_MUTATORS.has(name)) {
    const args = argv.slice(at + 1);
    return { targets: name === 'ln' ? linkTargets(args) : destinationTargets(args) };
  }
  if (['sed', 'perl'].includes(name) && hasInPlaceFlag(argv.slice(at + 1))) {
    return { targets: inPlaceTargets(name, argv.slice(at + 1)) };
  }
  if (name === 'dd') {
    return { targets: argv.slice(at + 1).filter((word) => word.startsWith('of=')).map((word) => word.slice(3)) };
  }
  if (name === 'git') {
    const subcommand = argv[at + 1] || '';
    if (!GIT_MUTATORS.has(subcommand)) return null;
    if (subcommand === 'stash') return { targets: [] };
    return { targets: pathArguments(argv.slice(at + 2)) };
  }
  return null;
}

function destinationTargets(argv) {
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '-t' || argv[i] === '--target-directory') {
      return argv[i + 1] ? [argv[i + 1]] : [];
    }
    if (argv[i].startsWith('--target-directory=')) {
      const target = argv[i].slice('--target-directory='.length);
      return target ? [target] : [];
    }
  }
  const paths = pathArguments(argv);
  return paths.length ? [paths.at(-1)] : [];
}

function linkTargets(argv) {
  const paths = [];
  let destination = '';
  for (let i = 0; i < argv.length; i += 1) {
    const word = argv[i];
    if (word === '-t' || word === '--target-directory') {
      destination = argv[i + 1] || '';
      i += 1;
    } else if (word.startsWith('--target-directory=')) {
      destination = word.slice('--target-directory='.length);
    } else if (!word.startsWith('-')) {
      paths.push(word);
    }
  }
  if (!destination && paths.length) destination = paths.at(-1);
  return destination ? [destination] : [];
}

function hasInPlaceFlag(argv) {
  return argv.some((word) => word === '--in-place'
    || word.startsWith('--in-place=')
    || /^-[A-Za-z]*i/.test(word));
}

// In-place editors mutate input files, not their program/expression operand.
// Parse only enough option structure to identify those file operands; if the
// command is too incomplete to expose one, the caller keeps it fail-closed.
function inPlaceTargets(name, argv) {
  const positional = [];
  let scriptSpecified = false;
  let literal = false;
  for (let i = 0; i < argv.length; i += 1) {
    const word = argv[i];
    if (literal) {
      positional.push(word);
      continue;
    }
    if (word === '--') {
      literal = true;
      continue;
    }
    if (name === 'sed' && ['-e', '--expression', '-f', '--file'].includes(word)) {
      scriptSpecified = true;
      i += 1;
      continue;
    }
    if (name === 'perl' && (word === '-e' || (/^-[A-Za-z]+$/.test(word) && word.includes('e')))) {
      scriptSpecified = true;
      i += 1;
      continue;
    }
    if (word === '-i' && argv[i + 1] === '') {
      i += 1;
      continue;
    }
    if (word.startsWith('-')) continue;
    positional.push(word);
  }
  return scriptSpecified ? positional : positional.slice(1);
}

function pathArguments(argv) {
  const paths = [];
  let literal = false;
  for (let i = 0; i < argv.length; i += 1) {
    const word = argv[i];
    if (literal) {
      paths.push(word);
      continue;
    }
    if (word === '--') {
      literal = true;
      continue;
    }
    if (word.startsWith('-')) {
      if (VALUE_FLAGS.has(word)) i += 1;
      continue;
    }
    paths.push(word);
  }
  return paths;
}

// Цели одного куска команды. Разбор целей не зависит от того, где кусок стоит:
// зависит только резолв относительного пути, и он живёт снаружи.
function pieceTargets(piece) {
  const targets = [];
  for (const m of matchAll(piece, />>?[ \t]*[^|&;()<>\s]+/.source)) {
    targets.push(m.replace(/^>>?[ \t]*/, ''));
  }
  for (const m of matchAll(piece, /\btee\b([ \t]+-[a-zA-Z]+)*[ \t]+[^|&;()<>\s]+/.source)) {
    targets.push(lastField(m));
  }
  for (const m of matchAll(piece, /\b(cp|mv)\b[^|&;]*[ \t]-t[ \t]+[^\s|&;]+/.source)) {
    targets.push(m.replace(/^.*[ \t]-t[ \t]+/, ''));
  }
  if (!/[ \t]-t[ \t]/.test(piece)) {
    for (const m of matchAll(piece, /\b(cp|mv)\b[ \t]+[^|&;()<>]+/.source)) {
      targets.push(lastField(m));
    }
  }
  return targets;
}

// Запись из интерпретатора ищется в ИСХОДНОЙ команде, а не в очищенном тексте:
// путь там стоит в кавычках, и на вычеркнутой строке разбор бы ослеп. По кускам
// такие цели не разложить — регулярка смотрит на весь текст, — поэтому они
// собираются отдельно и каталогом перехода не резолвятся: интерпретатор
// запускается со своим рабочим каталогом, и угадывать его разбор не берётся.
function interpreterTargets(cmd) {
  const targets = [];
  for (const m of matchAll(cmd, /open\([ \t]*['"][^'"]+['"][ \t]*,[ \t]*['"][wa]/.source)) {
    targets.push(m.replace(/^open\([ \t]*['"]/, '').replace(/['"].*$/, ''));
  }
  for (const m of matchAll(cmd, /Path\([ \t]*['"][^'"]+['"][ \t]*\)[ \t]*\.[ \t]*write_(text|bytes)/.source)) {
    targets.push(m.replace(/^Path\([ \t]*['"]/, '').replace(/['"].*$/, ''));
  }
  return targets;
}

// Цель записи, приведённая к настоящему пути. Команда часто переходит в каталог
// и пишет уже относительным именем: «cd /tmp/work && cat > notes.md». Цель,
// взятая как написана, начинается не с /tmp — и запись во временный каталог
// гейтилась, хотя та же запись абсолютным путём проходила свободно.
//
// База берётся из АБСОЛЮТНОГО cd: относительный («cd ..») перевёл бы из
// каталога, которого разбор не знает, и склейка соврала бы.
//
// Путь НОРМАЛИЗУЕТСЯ: без этого «cd /tmp && cat > ../repo/README.md» давал
// /tmp/../repo/README.md, и эфемерность решалась по префиксу /tmp — правка
// репозитория проходила бы гейт через переход вверх.
function resolveTarget(cwd, target) {
  if (!target || target.startsWith('-')) return target;
  const joined = target.startsWith('/') || !cwd
    ? target
    : `${cwd}/${target.replace(/^\.\//, '')}`;
  return joined.startsWith('/') ? normalizePath(joined) : joined;
}

// Свёртка «..» и «.» в абсолютном пути. Свой разбор, а не path.posix.normalize:
// поведение здесь должно быть одинаковым для гейта и read-only-классификатора
// независимо от платформы.
function normalizePath(fp) {
  const parts = [];
  for (const part of fp.split('/')) {
    if (part === '' || part === '.') continue;
    if (part === '..') {
      parts.pop();
      continue;
    }
    parts.push(part);
  }
  return `/${parts.join('/')}`;
}

export function normalizeTarget(fp) {
  if (typeof fp !== 'string') return '';
  const value = fp.replaceAll('\\', '/');
  if (value.startsWith('/')) return normalizePath(value);
  const parts = [];
  for (const part of value.split('/')) {
    if (part === '' || part === '.') continue;
    if (part === '..') parts.pop();
    else parts.push(part);
  }
  return parts.join('/');
}

// Цель записи, очищенная от кавычек; дескрипторы и устройства целями не
// являются и отсеиваются здесь же — пустая строка означает «это не цель».
export function cleanTarget(rawTarget) {
  if (typeof rawTarget !== 'string' || !/\S/.test(rawTarget)) return '';
  const unquoted = rawTarget.replace(/"$/, '').replace(/^"/, '').replace(/'$/, '').replace(/^'/, '');
  if (unquoted.startsWith('-') || ['0', '1', '2', '&1', '&2'].includes(unquoted)) return '';
  const normalized = normalizeTarget(unquoted);
  if (normalized.startsWith('/dev/')) return '';
  return normalized;
}
