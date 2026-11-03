"""База модуля свежести: сверить набор с удалённым и сказать, если он отстал.

База ничего не ставит и не меняет. Она читает журнал установки, сверяет каждый
источник набора с его удалённой главной веткой и собирает одну строку для
модели: окружение надо обновить, вот версии, а вот что проверить не удалось.

Сверяется не голова репозитория, а коммиты в файлы набора: коммит мимо набора
ничего в нём не меняет и тревожить не должен.

- Главный набор (craft) — по файлам, которые ставит установщик: `modules`,
  `core` и сам установщик, против main у origin.
- Дополнительный источник (junk в Аркадии и любой другой) — по своей папке
  целиком против главной ветки своей системы: коммит папки установщик пишет в
  журнал рядом с её путём, в `extra_origins` источника.

Исходы сверки одного источника:

- FRESH — файлы набора на удалённом те же;
- STALE — удалённое ушло вперёд, набор отстал; об этом и говорим;
- AWAY — набор поставлен из ветки или модуля на удалённом нет: это выбор
  человека, а не отставание, молчим;
- UNKNOWN — проверить не вышло; говорим с причиной.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from jarvis import vcs

# Что ставит установщик: модули, ядро внутри каждого и сам установщик, который
# решает, куда что ляжет.
SET_PATHS = ('modules', 'core', 'tools/jarvis-install')
LEDGER_SOURCE = 'source'
LEDGER_MODULES = 'modules'
LEDGER_EXTRA = 'extra'
LEDGER_EXTRA_ORIGINS = 'extra_origins'

FRESH = 'fresh'
STALE = 'stale'
AWAY = 'away'
UNKNOWN = 'unknown'

SAME = 'совпадает с {remote}'
UNTOUCHED = 'в {remote} файлы набора не менялись'
ABSENT = 'в {remote} этих файлов нет'
UNCLEAR = 'не узнать, отстал ли {old} от {remote} {new}'
OWN_CHOICE = 'поставлен из {old}, это не отставание от {remote}'
NO_HEAD = 'журнал не назвал коммит источника'
NOT_GIT = 'источник {path} не git-репозиторий'
NO_COMMIT = 'журнал не назвал коммит папки источника'
NO_VCS = 'папка {path} не под git и не под arc'
EXTRA_UNRECORDED = ('дополнительный источник поставлен без коммита в журнале — поставь '
                    'набор заново, чтобы его можно было сверять')

STALE_HEAD = ('Окружение надо обновить: набор модулей агента отстал. Модуль свежести '
              'только предупреждает и ничего не меняет.')
STALE_ITEM = '{name}: стоит {installed}, в {remote} {latest}'
HOW_TO = ('Обновить — поставить набор заново установщиком tools/jarvis-install из '
          'свежего источника с прежними флагами.')
UNKNOWN_HEAD = 'Свежесть набора проверить не удалось — {items}.'


@dataclass(frozen=True)
class Finding:
    """Итог сверки одного источника набора."""

    name: str
    state: str
    reason: str = ''
    remote: str = ''
    installed: vcs.Version | None = None
    latest: vcs.Version | None = None
    changed: str = ''
    old: str | None = None
    new: str | None = None


class LedgerError(Exception):
    """Журнал есть, но не читается."""


def read_ledger(path: Path) -> tuple[dict, dict] | None:
    """Журнал установки: чем набор поставлен и что в нём стоит. Нет — None.

    Журнал прежней плоской формы (записи модулей без источника) читается как
    набор с неизвестным источником.
    """
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    try:
        ledger = json.loads(text)
    except ValueError as failure:
        raise LedgerError(f'журнал установки не читается: {failure}') from failure
    modules = ledger.get(LEDGER_MODULES)
    # Запись модуля всегда несёт свой каталог: по этому журнал прежней плоской
    # формы отличается от нынешнего, у которого источник и модули врозь.
    if isinstance(modules, dict) and 'dir' not in modules:
        source = ledger.get(LEDGER_SOURCE)
        return (source if isinstance(source, dict) else {}), modules
    return {}, ledger


def describe(paths: list[str], modules_dir: str | None = 'modules') -> str:
    """Что изменилось: модули по slug, остальное — по верхнему каталогу.

    `modules_dir` — где в путях лежат модули: у набора craft это `modules/`, у
    дополнительного источника пути идут от его папки, и модуль — первый каталог.
    """
    slugs, areas = set(), set()
    for path in paths:
        parts = path.strip().split('/')
        if modules_dir is None and len(parts) > 1:
            slugs.add(parts[0])
        elif len(parts) > 2 and parts[0] == modules_dir:
            slugs.add(parts[1])
        elif parts[0]:
            areas.add(parts[0])
    told = []
    if slugs:
        told.append('изменились модули: ' + ', '.join(sorted(slugs)))
    if areas:
        told.append('и остальное: ' + ', '.join(sorted(areas)))
    return ' '.join(told)


def compare(name: str, found, old: str, modules_dir: str | None = 'modules') -> Finding:
    """Сверить установленную ревизию с удалённой головой по файлам источника."""
    remote = found.remote_name
    short = old[:12]
    try:
        new = found.remote()
        if new == old:
            return Finding(name, FRESH, SAME.format(remote=remote), remote, old=old, new=new)
        changed = found.changed(old, new)
        if not changed:
            return Finding(name, FRESH, UNTOUCHED.format(remote=remote), remote, old=old, new=new)
        latest = found.version(new)
        if latest is None:
            return Finding(name, AWAY, ABSENT.format(remote=remote), remote, old=old, new=new)
        installed = found.version(old)
        behind = found.behind(old, new, installed, latest)
    except vcs.VcsError as failure:
        return Finding(name, UNKNOWN, str(failure), remote, old=old)
    if behind is None:
        return Finding(name, UNKNOWN, UNCLEAR.format(old=short, remote=remote, new=new[:12]),
                       remote, old=old, new=new)
    if not behind:
        return Finding(name, AWAY, OWN_CHOICE.format(old=short, remote=remote), remote,
                       old=old, new=new)
    return Finding(name, STALE, '', remote, installed, latest,
                   describe(changed, modules_dir), old=old, new=new)


def check_set(source: dict, where: Path | None = None) -> Finding:
    """Главный набор против main. `where` — чекаут, если он известнее журнала."""
    path = where or Path(source.get('path') or '.')
    name = f'набор {path}'
    old = source.get('head')
    if not old:
        return Finding(name, UNKNOWN, NO_HEAD)
    try:
        root = vcs.run(['git', 'rev-parse', '--show-toplevel'], path).stdout.strip()
    except vcs.VcsError:
        return Finding(name, UNKNOWN, NOT_GIT.format(path=path))
    return compare(f'набор {root}', vcs.Git(Path(root), SET_PATHS), old)


def check_extras(source: dict) -> list[Finding]:
    """Дополнительные источники: каждый по коммитам в свою папку целиком."""
    origins = [origin for origin in source.get(LEDGER_EXTRA_ORIGINS) or []
               if isinstance(origin, dict) and origin.get('path')]
    recorded = {origin['path'] for origin in origins}
    # Журнал до записи коммитов источников: источник есть, сверять не с чем.
    unrecorded = [Finding(f'источник {path}', UNKNOWN, EXTRA_UNRECORDED)
                  for path in source.get(LEDGER_EXTRA) or [] if path not in recorded]
    return [*map(check_origin, origins), *unrecorded]


def check_origin(origin: dict) -> Finding:
    name = f'источник {origin["path"]}'
    if not origin.get('commit'):
        return Finding(name, UNKNOWN, NO_COMMIT)
    found = vcs.detect(Path(origin['path']))
    if found is None:
        return Finding(name, UNKNOWN, NO_VCS.format(path=origin['path']))
    return compare(name, found, origin['commit'], modules_dir=None)


def check_all(source: dict, where: Path | None = None) -> list[Finding]:
    """Все источники набора: главный первым, за ним дополнительные."""
    return [check_set(source, where), *check_extras(source)]


def warning(findings: list[Finding]) -> str:
    """Строка для модели: что отстало и что проверить не удалось. Нечего — пусто."""
    stale = [f for f in findings if f.state == STALE]
    unknown = [f for f in findings if f.state == UNKNOWN]
    told = []
    if stale:
        items = []
        for finding in stale:
            item = STALE_ITEM.format(
                name=finding.name, remote=finding.remote,
                installed=finding.installed.label() if finding.installed else finding.old[:12],
                latest=finding.latest.label(),
            )
            items.append(f'{item} — {finding.changed}' if finding.changed else item)
        told += [STALE_HEAD, '; '.join(items) + '.', HOW_TO]
    if unknown:
        told.append(UNKNOWN_HEAD.format(
            items='; '.join(f'{f.name}: {f.reason}' for f in unknown)))
    return ' '.join(told)


def journal(findings: list[Finding]) -> str:
    """Сверка одной строкой для журнала модуля: исход и причина по источнику."""
    return '; '.join(f'{f.name}: {f.state}' + (f' ({f.reason})' if f.reason else '')
                     for f in findings)
