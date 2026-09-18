"""Разбор команды оболочки и ответ «пишет ли она и куда».

Работу делает bash-classify (MIT, Filip Procházka): tree-sitter-bash плюс база
правил на 150+ команд. Здесь — вызов его процессом, рабочий каталог звена,
которого у него нет, и наша форма ответа.

Молчаливого «только читает» тут нет: нет утилиты, не разобрался JSON, упал
вызов — наружу идёт исключение. Замок, получивший такой ответ молча, пропустил
бы удаление каталога, и человек об этом не узнал бы.

Два слепых пятна bash-classify 0.11.0 замерены на живом в этом окружении
18.09.2026, и оба врут в опасную сторону — запись показывают чтением:

- перенаправление ВНУТРИ строки `bash -c` теряется: `bash -c "cat > README.md"`
  отвечает READONLY без write_paths, хотя `cat > README.md` отдельно —
  LOCAL_EFFECTS с целью. Лечим тем же инструментом: делегированную строку
  берём из argv и отдаём bash-classify отдельным вызовом;
- рядом с heredoc теряется перенаправление вывода: `cat <<EOF > f.txt` —
  READONLY, в redirects только `<<`. Лечить нечем, поэтому команда с heredoc
  не может получить ответ «нет»: её вердикт — «неизвестно».

Страховка сделана так, чтобы она никогда не добавляла записи, которой нет:
первое — повторный вызов той же утилиты, второе — понижение «нет» до
«неизвестно».
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

BINARY_ENV = 'BASH_CLASSIFY_BIN'
BINARY_NAME = 'bash-classify'
DEFAULT_BINARY = '~/.local/bin/bash-classify'
# Разбор — работа на доли секунды; секунды означают, что что-то пошло не так, а
# замок стоит перед вызовом инструмента и ждать не может.
CALL_TIMEOUT = 15
# Глубина, на которую разворачиваем вложенные строки оболочки. Строка внутри
# строки внутри строки — уже не работа, а попытка спрятаться.
MAX_DEPTH = 4

WRITES_YES = 'да'
WRITES_NO = 'нет'
WRITES_UNKNOWN = 'неизвестно'

# Классификация bash-classify → наш ответ «пишет ли». Сопоставление явное:
# READONLY у них означает «ничего не меняет», три средних класса различают, куда
# дотянется изменение, а UNKNOWN — «команды нет в базе правил», и это ровно наше
# «неизвестно», а не «читает».
WRITES_BY_CLASSIFICATION = {
    'READONLY': WRITES_NO,
    'LOCAL_EFFECTS': WRITES_YES,
    'EXTERNAL_EFFECTS': WRITES_YES,
    'DANGEROUS': WRITES_YES,
    'UNKNOWN': WRITES_UNKNOWN,
}

KIND_FILE = 'файл'
KIND_DIR = 'каталог'
KIND_GIT = 'git'
KIND_NETWORK = 'сеть'
KIND_PROCESS = 'процесс'

# Вид цели по правилу, которое назвал bash-classify (поля «вид» у него нет).
# Здесь только те правила, у которых цель видна в positionals: остальным
# ответом служит сам вердикт с причиной, а имя цели не выдумывается.
KIND_BY_RULE = {
    'rm': KIND_FILE, 'mv': KIND_FILE, 'cp': KIND_FILE, 'touch': KIND_FILE,
    'ln': KIND_FILE, 'install': KIND_FILE, 'tee': KIND_FILE, 'truncate': KIND_FILE,
    'shred': KIND_FILE, 'chmod': KIND_FILE, 'chown': KIND_FILE, 'chgrp': KIND_FILE,
    'mkdir': KIND_DIR, 'rmdir': KIND_DIR,
    'kill': KIND_PROCESS, 'pkill': KIND_PROCESS, 'killall': KIND_PROCESS,
    'curl': KIND_NETWORK, 'wget': KIND_NETWORK, 'scp': KIND_NETWORK,
    'rsync': KIND_NETWORK, 'ssh': KIND_NETWORK,
}
# Подкоманды git, которые ходят наружу; остальные меняют сам репозиторий.
GIT_NETWORK = ('push', 'fetch', 'pull', 'clone')
# Цели этих видов путями не являются, и каталог вызова к ним не применяется.
KINDS_WITHOUT_PATH = (KIND_PROCESS, KIND_NETWORK)

# Команды, меняющие рабочий каталог.
MOVES = ('cd', 'pushd', 'popd')
# Знаки, за которыми `cd` наружу не выходит: подоболочка, конвейер, фон. `&&` и
# `||` последовательны, и перед поиском они вычёркиваются.
ISOLATING = ('(', '|', '&')
HEREDOC_OPERATORS = ('<<', '<<-')


class ShellTreeError(RuntimeError):
    """Разбора не вышло. Текст говорит, что именно не получилось."""


def binary() -> str:
    """Где утилита. Не нашлась — исключение с тем, где искали."""
    named = os.environ.get(BINARY_ENV)
    if named and Path(named).expanduser().is_file():
        return str(Path(named).expanduser())
    default = Path(DEFAULT_BINARY).expanduser()
    if default.is_file():
        return str(default)
    found = shutil.which(BINARY_NAME)
    if found:
        return found
    raise ShellTreeError(
        f'{BINARY_NAME} не найден: ни в {BINARY_ENV}, ни в {DEFAULT_BINARY}, ни в PATH'
    )


def classify(command: str) -> dict:
    """Один вызов bash-classify: команда на stdin, JSON на stdout.

    Пустую строку сам bash-classify считает ошибкой входа (код 1), а выполнять
    в ней нечего, и ответ на неё известен без вызова.
    """
    if not command.strip():
        return {'expression': command, 'classification': 'READONLY', 'risk': None,
                'commands': [], 'empty': True}
    tool = binary()
    try:
        done = subprocess.run(
            [tool], input=command, capture_output=True, text=True,
            timeout=CALL_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ShellTreeError(f'{BINARY_NAME} не уложился в {CALL_TIMEOUT} с') from None
    except OSError as failure:
        raise ShellTreeError(f'{BINARY_NAME} не запустился: {failure}') from failure
    if done.returncode != 0:
        raise ShellTreeError(
            f'{BINARY_NAME} вернул {done.returncode}: {done.stderr.strip()[:300]}'
        )
    try:
        answer = json.loads(done.stdout)
    except json.JSONDecodeError as failure:
        raise ShellTreeError(
            f'ответ {BINARY_NAME} — не JSON: {failure}; начало ответа: {done.stdout[:200]!r}'
        ) from failure
    if not isinstance(answer, dict) or 'classification' not in answer:
        raise ShellTreeError(f'в ответе {BINARY_NAME} нет классификации: {done.stdout[:200]!r}')
    return answer


def parse(command: str, cwd: str = '') -> dict:
    """Разбор bash-classify как есть плюс наш ключ `cwd_walk`.

    В `cwd_walk` — каталог каждой команды по порядку и признак, можно ли этому
    каталогу верить.
    """
    data = classify(command)
    data['cwd_walk'] = cwd_walk(data, cwd)
    return data


def verdict(command: str, cwd: str = '') -> dict:
    """Пишет ли команда, куда и почему.

    Поля: `writes` (да, нет, неизвестно), `targets` (`path`, `kind`, `via`),
    `reason`, `notes` — что сделала страховка, плюс `classification` и `risk`
    bash-classify как есть.
    """
    return judge(command, cwd, depth=0)


def judge(command: str, cwd: str, depth: int) -> dict:
    """Вердикт по строке и по строкам, которые она запускает через `-c`."""
    data = parse(command, cwd)
    answer = verdict_of(data)
    if depth >= MAX_DEPTH:
        return answer
    for expression, where in delegated(data):
        inner = judge(expression, where, depth + 1)
        answer = merge(answer, inner, expression)
    return answer


def verdict_of(data: dict) -> dict:
    """Вердикт по одному ответу bash-classify, без вложенных строк."""
    classification = data.get('classification') or 'UNKNOWN'
    writes = WRITES_BY_CLASSIFICATION.get(classification, WRITES_UNKNOWN)
    reason = reason_of(data, classification)
    notes = []
    warnings = data.get('parse_warnings') or []
    if warnings:
        writes = WRITES_UNKNOWN
        reason = f'разбор не полон: {warnings[0]}'
    elif writes == WRITES_NO and heredoc(data):
        # Замер 18.09.2026: рядом с heredoc bash-classify теряет `>`.
        writes = WRITES_UNKNOWN
        reason = 'команда с heredoc: рядом с ним bash-classify теряет перенаправление вывода'
        notes.append('ответ «нет» понижен до «неизвестно» из-за heredoc')
    walk = data['cwd_walk']
    targets = targets_of(data)
    relative = [item for item in targets
                if item['kind'] not in KINDS_WITHOUT_PATH and not item['path'].startswith('/')]
    if relative:
        notes.append('цели даны относительно каталога вызова: ' + (
            walk['reason'] or 'в строке есть переход каталога, а какой команде '
                              'принадлежит перенаправление, bash-classify не говорит'))
    return {
        'writes': writes,
        'targets': targets,
        'reason': reason,
        'notes': notes,
        'classification': classification,
        'risk': data.get('risk'),
    }


def reason_of(data: dict, classification: str) -> str:
    """Причина словами самого bash-classify: он называет правило и почему."""
    for command in deep_commands(data):
        if command.get('classification') in (None, 'READONLY'):
            continue
        name = (command.get('argv') or [''])[0]
        said = command.get('classification_reason') or f'правило {command.get("matched_rule")}'
        return f'{classification}: «{name}» — {said}'
    if data.get('empty'):
        return 'выполнять нечего: команд в строке нет'
    return f'{classification}: ни одна команда строки ничего не меняет'


def merge(answer: dict, inner: dict, expression: str) -> dict:
    """Ответ строки и ответ того, что она запускает, сводятся строжайшим."""
    order = (WRITES_NO, WRITES_UNKNOWN, WRITES_YES)
    merged = dict(answer)
    merged['targets'] = dedupe(answer['targets'] + inner['targets'])
    merged['notes'] = answer['notes'] + inner['notes']
    if order.index(inner['writes']) > order.index(answer['writes']):
        merged['writes'] = inner['writes']
        merged['reason'] = f'внутри строки «{expression}»: {inner["reason"]}'
        merged['notes'] = merged['notes'] + [
            'строка оболочки разобрана отдельным вызовом: bash-classify теряет в ней перенаправления'
        ]
    return merged


def delegated(data: dict):
    """Строки, которые команда отдаёт оболочке ключом `-c`, и их каталог.

    bash-classify показывает такую строку разобранной (`delegation_mode`
    = flag_value_is_expression), но перенаправления в ней теряет, поэтому саму
    строку мы отдаём ему ещё раз отдельным вызовом.
    """
    walk = data['cwd_walk']
    for index, command in enumerate(data.get('commands') or []):
        where = walk['where'][index] if index < len(walk['where']) else ''
        for node in with_inner(command):
            for inner in node.get('inner_commands') or []:
                if inner.get('delegation_mode') != 'flag_value_is_expression':
                    continue
                argv = node.get('argv') or []
                flag = inner.get('delegation_source')
                if flag in argv and argv.index(flag) + 1 < len(argv):
                    yield argv[argv.index(flag) + 1], where


def with_inner(command: dict):
    """Команда и все её вложенные команды, сверху вниз."""
    yield command
    for inner in command.get('inner_commands') or []:
        yield from with_inner(inner)


def deep_commands(data: dict):
    for command in data.get('commands') or []:
        yield from with_inner(command)


def heredoc(data: dict) -> bool:
    return any((redirect.get('operator') or '') in HEREDOC_OPERATORS
               for redirect in data.get('redirects') or [])


def targets_of(data: dict) -> list:
    """Цели записи: пути из перенаправлений и цели, видные по правилу команды."""
    walk = data['cwd_walk']
    targets = []
    for index, command in enumerate(data.get('commands') or []):
        where = walk['where'][index] if index < len(walk['where']) else ''
        for node in with_inner(command):
            for path in node.get('write_paths') or []:
                targets.append(target(path, KIND_FILE, 'перенаправление', redirect_cwd(walk)))
            targets += rule_targets(node, where)
    for path in data.get('write_paths') or []:
        targets.append(target(path, KIND_FILE, 'перенаправление', redirect_cwd(walk)))
    return dedupe([item for item in targets if item])


def rule_targets(command: dict, where: str) -> list:
    """Цели, которые видно по правилу bash-classify и его же positionals."""
    if command.get('classification') in (None, 'READONLY'):
        return []
    rule = command.get('matched_rule') or ''
    positionals = command.get('positionals') or []
    if rule.startswith('git.'):
        subcommand = rule.split('.', 1)[1]
        if subcommand in GIT_NETWORK:
            remote = positionals[0] if positionals else ''
            return [{'path': remote, 'kind': KIND_NETWORK, 'via': rule}]
        return [{'path': where, 'kind': KIND_GIT, 'via': rule}] if where else []
    kind = KIND_BY_RULE.get(rule)
    if kind is None:
        return []
    if rule == 'rm' and recursive(command):
        kind = KIND_DIR
    return [item for item in (target(path, kind, rule, where) for path in positionals) if item]


def recursive(command: dict) -> bool:
    for option in command.get('options') or []:
        if option in ('--recursive', '--dir'):
            return True
        if option.startswith('-') and not option.startswith('--') and set('rRd') & set(option[1:]):
            return True
    return False


def target(path: str, kind: str, via: str, where: str):
    """Цель записи. Путь, который целиком знает только оболочка (`$OUT`,
    подстановка команды), целью не называется: имя файла тут выдумано бы. Сама
    запись при этом из вердикта не пропадает — она в `writes` и в причине."""
    if kind not in KINDS_WITHOUT_PATH and ('$' in path or '`' in path):
        return None
    return {'path': resolve(path, where) if kind not in KINDS_WITHOUT_PATH else path,
            'kind': kind, 'via': via}


def resolve(path: str, where: str) -> str:
    """Путь от каталога звена. Каталог неизвестен — путь остаётся как есть:
    выдуманный корень назвал бы файл, которого никто не писал."""
    if path.startswith('/'):
        return os.path.normpath(path)
    if not where:
        return path
    return os.path.normpath(os.path.join(where, path))


def dedupe(targets: list) -> list:
    seen = set()
    out = []
    for item in targets:
        key = (item['kind'], item['path'])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def cwd_walk(data: dict, cwd: str) -> dict:
    """Рабочий каталог каждой команды: этого bash-classify не считает.

    Команды он отдаёт плоским списком в порядке текста, но границ подоболочек,
    конвейеров и фона в JSON нет, а за ними `cd` наружу не выходит:
    `(cd /tmp) && rm b` и `cd /tmp && rm b` в его ответе неразличимы. Поэтому,
    когда в строке есть и переход каталога, и такой знак, каталог считается
    непроведённым, и цели остаются относительными — лучше без каталога, чем с
    чужим.
    """
    commands = data.get('commands') or []
    expression = data.get('expression') or ''
    moves = [command for command in commands if head(command) in MOVES]
    trusted, reason = True, ''
    if moves and isolating(expression):
        trusted, reason = False, ('в строке есть подоболочка, конвейер или фон, '
                                  'а границ звеньев bash-classify не отдаёт')
    where = []
    current = cwd if trusted else ''
    stack = []
    if trusted and not cwd:
        # Корень неизвестен, но `cd /tmp` внутри строки известен сам по себе:
        # относительные пути после него называются полностью.
        reason = 'каталог вызова не задан'
    
    for command in commands:
        where.append(current)
        if not trusted:
            continue
        name = head(command)
        argv = command.get('argv') or []
        if name == 'cd':
            current = moved(current, argv[1:])
        elif name == 'pushd':
            stack.append(current)
            current = moved(current, argv[1:])
        elif name == 'popd':
            current = stack.pop() if stack else ''
    return {'cwd': cwd, 'trusted': trusted, 'reason': reason, 'where': where,
            'moves': bool(moves)}


def redirect_cwd(walk: dict) -> str:
    """Каталог, от которого читается путь перенаправления.

    Владельца перенаправления bash-classify не называет: замер 18.09.2026 —
    в `cd /tmp && cat > inner.txt` и в `cat a.txt | grep x > out.txt` цель
    приписана ПЕРВОЙ команде строки, а не той, при которой стоит. Поэтому путь
    читается от каталога вызова, а если в строке есть переход каталога —
    не читается вовсе: по какую сторону от `cd` стоит запись, неизвестно.
    """
    if not walk['trusted'] or walk['moves']:
        return ''
    return walk['cwd']


def head(command: dict) -> str:
    argv = command.get('argv') or []
    return argv[0] if argv else ''


def moved(current: str, arguments: list) -> str:
    """Каталог после `cd`. Аргумента нет, он ключ или он с подстановкой —
    каталог становится неизвестным и таким остаётся до следующего понятного."""
    for argument in arguments:
        if argument.startswith('-'):
            continue
        if '$' in argument or '~' in argument or '`' in argument:
            return ''
        return resolve(argument, current)
    return ''


def isolating(expression: str) -> bool:
    """Есть ли в строке знак, за которым `cd` наружу не выходит. `&&` и `||`
    последовательны, поэтому вычёркиваются до поиска."""
    plain = expression.replace('&&', '').replace('||', '')
    return any(sign in plain for sign in ISOLATING)
