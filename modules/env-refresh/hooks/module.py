#!/usr/bin/env python3
"""Модуль env-refresh: набор модулей догоняет main на старте облачной сессии.

Зачем. Модули ставит setup-скрипт окружения, а он выполняется один раз — при
сборке снимка окружения; дальше сессии стартуют из снимка, пропуская скрипт
(дока cloud-environments, раздел Environment caching, 19.09.2026). Код
репозитория платформа отдаёт свежим на каждом старте, а результат установки в
домашнем каталоге остаётся тем, каким его застал снимок: до недели старый.
Этот модуль сверяет набор с main и, если он отстал, ставит набор заново.

Где он работает. Только в облачной сессии Claude Code on the web: кеш
окружения — её особенность. Без признака `CLAUDE_CODE_REMOTE` модуль молчит.

Какая копия работает. Модуль лежит в двух местах сразу: установленной копией в
каталоге настроек и исходником в чекауте репозитория, откуда его зовёт строка
хука из `.claude/settings.json` самого репозитория. Работает только копия из
чекаута: она приехала с чекаутом и потому свежая по построению, а установленная
копия — ровно то, что мы и собираемся заменить. Установленная копия узнаёт себя
по тому, что над ней нет репозитория с `tools/jarvis-install`, пишет строку в
свой журнал и молчит.

По чему сверяется. Голова main спрашивается у origin одним запросом
`git ls-remote` — качать объекты ради сверки не нужно. Сравнивается она с
коммитом источника из журнала установки `installed.json`: без записи источника
свежесть набора не с чем сопоставить.

Что не трогается. Рабочее дерево сессии, её HEAD и ветка: ни checkout, ни
reset, ни смены ветки. `git fetch` доносит объекты в хранилище клона, а сам
набор ставится из распаковки main рядом. Ветка сессии в набор не попадает
никогда: разработка — не применение, и работает агент всегда по main.

Провал ничего не портит. Прежний набор уходит в сторону одним переименованием и
возвращается на место, если установка не дошла до конца. Запасных путей нет:
либо набор обновился, либо в контекст уходит одна строка с причиной.

Успех останавливает старт. Контекст этого старта собран прежним набором, и
работать по нему нельзя: модуль печатает остановку и просит человека сжать
контекст. После сжатия событие приходит уже с `source = compact`, на нём модуль
не стоит, и старт печатается по новому набору.

Что модуль знает и чего не знает. Он знает только про свежесть окружения: ни
про Craft, ни про поставщиков данных, ни про стартовый контекст — и в их
цепочку не встраивается.
"""

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

_MODULE_DIR = Path(__file__).resolve().parents[1]
# Установленная копия везёт ядро внутри себя; у исходника в чекауте своего ядра
# нет — там оно общее, в `core/` репозитория.
for _core in (_MODULE_DIR / '_core', _MODULE_DIR.parents[1] / 'core'):
    if _core.is_dir():
        sys.path.insert(0, str(_core))
        break

import jarvis  # noqa: E402
from jarvis import wrappers  # noqa: E402

# Признак облачной сессии Claude Code on the web.
ENV_CLOUD = 'CLAUDE_CODE_REMOTE'
CLOUD = 'true'
# Каталог настроек харнеса задаёт сам харнес, а не прошлая установка: ставим
# туда, откуда идущая сессия читает настройки.
ENV_SETTINGS = 'CLAUDE_CONFIG_DIR'
SETTINGS_DEFAULT = '~/.claude'
LEDGER = ('jarvis', 'installed.json')
LEDGER_SOURCE = 'source'
LEDGER_MODULES = 'modules'
JARVIS_DIR = 'jarvis'
HARNESS_PARTS = ('skills', 'agents', 'rules')
SETTINGS_FILE = 'settings.json'
# Склад самого модуля: собранное им лежит тут, частью модуля не является, и
# переустановка его не трогает.
BUILT_DIR = 'bin'
INSTALLER = ('tools', 'jarvis-install')
HARNESS_DEFAULT = 'claude'

GIT = 'git'
ORIGIN = 'origin'
MAIN_REF = 'refs/heads/main'
# Пределы у каждого шага свои и все вместе заведомо меньше срока хука: хук,
# убитый харнесом посреди установки, вернуть набор на место уже не успеет.
LS_REMOTE_TIMEOUT_SEC = 120
FETCH_TIMEOUT_SEC = 600
INSTALL_TIMEOUT_SEC = 600

JOURNAL = 'env-refresh.jsonl'
NOT_CLOUD = 'не облачная сессия'
INSTALLED_COPY = 'запуск не из чекаута: набор обновляет копия из чекаута'
SAME = 'набор уже стоит по main {head}'
UPDATED = 'набор обновлён с {old} на {new}'

STOP = (
    'СТОП. Набор модулей агента только что обновлён с {old} на {new} — {changed}. '
    'Контекст этого старта собран прежним набором: правила, скиллы и данные в нём '
    'устарели, и работать по ним нельзя. Не начинай работу и не отвечай по существу '
    'запроса. Скажи человеку одной строкой, что набор обновлён и нужно сжать контекст '
    '(/compact), и на этом закончи ход: после сжатия старт повторится по новому набору.'
)
FAILED = (
    'Набор модулей не удалось догнать до main: {reason}. Работаем прежним набором '
    '(источник {old}) — он цел.'
)
UNKNOWN_HEAD = 'прошлая установка не назвала коммит'
CHANGED_UNKNOWN = 'состав изменений не сравнить: прошлого коммита в клоне нет'


class Trouble(Exception):
    """Причина, по которой набор остался прежним. Текст идёт человеку как есть."""


def checkout_of(entry: Path) -> Path | None:
    """Чекаут репозитория, из которого запущен этот файл. Не из чекаута — None.

    Признак — установщик на своём месте над папкой модуля: только рядом с ним
    лежит и код, которым ставить, и модули, которые ставить.
    """
    root = entry.resolve().parents[3]
    return root if root.joinpath(*INSTALLER).is_file() else None


def settings_dir(env) -> Path:
    """Каталог настроек идущего харнеса."""
    return Path(env.get(ENV_SETTINGS) or SETTINGS_DEFAULT).expanduser()


def installed(settings: Path) -> tuple[dict, dict]:
    """Журнал прошлой установки: чем набор поставлен и что в нём стоит.

    Журнала нет — набор считается неизвестным и ставится заново: это тот же
    случай, что устаревший, и запасного пути тут нет.
    """
    try:
        text = settings.joinpath(*LEDGER).read_text(encoding='utf-8')
    except OSError:
        return {}, {}
    try:
        ledger = json.loads(text)
    except ValueError as failure:
        raise Trouble(f'журнал установки не читается: {failure}') from failure
    modules = ledger.get(LEDGER_MODULES)
    # Запись модуля всегда несёт свой каталог: по этому журнал прежней плоской
    # формы отличается от нынешнего, у которого источник и модули врозь.
    if isinstance(modules, dict) and 'dir' not in modules:
        source = ledger.get(LEDGER_SOURCE)
        return (source if isinstance(source, dict) else {}), modules
    return {}, ledger


def git(checkout: Path, *args: str, timeout: int) -> str:
    """Вызов git в чекауте. Не вышло — Trouble с причиной, а не пустой ответ."""
    try:
        done = subprocess.run(
            [GIT, '-C', str(checkout), *args],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
    except FileNotFoundError as failure:
        raise Trouble('в окружении нет git') from failure
    except (OSError, subprocess.SubprocessError) as failure:
        raise Trouble(f'git {args[0]} не отработал: {type(failure).__name__}: {failure}') from failure
    if done.returncode != 0:
        raise Trouble(f'git {args[0]} вернул {done.returncode}: {done.stderr.strip()[:300]}')
    return done.stdout


def remote_head(checkout: Path) -> str:
    """Голова main у origin. Один запрос, объекты не качаются."""
    out = git(checkout, 'ls-remote', ORIGIN, MAIN_REF, timeout=LS_REMOTE_TIMEOUT_SEC)
    first = out.split('\n', 1)[0].split('\t', 1)[0].strip()
    if not first:
        raise Trouble(f'у origin нет ветки main (ссылка {MAIN_REF})')
    return first


def unpack(checkout: Path, head: str, into: Path) -> None:
    """Донести объекты main и развернуть его рядом, не трогая рабочее дерево."""
    git(checkout, 'fetch', '--quiet', ORIGIN, MAIN_REF, timeout=FETCH_TIMEOUT_SEC)
    archive = into / 'main.tar'
    git(checkout, 'archive', '--format=tar', '-o', str(archive), head,
        timeout=FETCH_TIMEOUT_SEC)
    with tarfile.open(archive) as bundle:
        bundle.extractall(into, filter='data')
    archive.unlink()


def changed(checkout: Path, old: str | None, new: str) -> str:
    """Чем новый набор отличается от прежнего: модули и прочие каталоги источника."""
    if not old:
        return CHANGED_UNKNOWN
    try:
        # `-z`: имена разделены нулём и не экранируются. Без него git закавычит
        # любой путь с не-ASCII символом, и первым сегментом станет кавычка.
        out = git(checkout, 'diff', '--name-only', '-z', old, new,
                  timeout=LS_REMOTE_TIMEOUT_SEC)
    except Trouble:
        return CHANGED_UNKNOWN
    slugs, areas = set(), set()
    for line in out.split('\0'):
        parts = line.strip().split('/')
        if len(parts) > 2 and parts[0] == 'modules':
            slugs.add(parts[1])
        elif parts[0]:
            areas.add(parts[0])
    told = []
    if slugs:
        told.append('изменились модули: ' + ', '.join(sorted(slugs)))
    if areas:
        told.append('и остальное: ' + ', '.join(sorted(areas)))
    return ' '.join(told) if told else 'файлы набора не изменились'


class Aside:
    """Прежний набор, отставленный в сторону на время установки.

    Отставляется переименованием, а не копией: набор весит мегабайты, а рядом с
    ним лежит бинарник, который его модуль собирает себе сам. Не дошла
    установка — набор возвращается на место целиком; дошла — отставленное
    удаляется.
    """

    def __init__(self, settings: Path, previous: dict) -> None:
        self.settings = settings
        self.store = settings / f'.jarvis-aside-{os.getpid()}'
        self.moved: list[tuple[Path, Path]] = []
        self.settings_file = settings / SETTINGS_FILE
        # Был ли файл настроек до нас, помнится отдельно от того, успели ли мы
        # снять копию: иначе отставление, упавшее до копии, на возврате
        # прочиталось бы как «файла и не было» и снесло бы живые настройки.
        self.had_settings = self.settings_file.is_file()
        self.settings_copy: Path | None = None
        self.places = self._places(previous)
        # Что лежало в местах харнеса до нас: по этому списку убирается то, что
        # оставила за собой неудачная установка.
        self.before = {part: self._names(settings / part) for part in HARNESS_PARTS}

    def _places(self, previous: dict) -> list[Path]:
        """Всё, что принадлежит прежнему набору: каталог Джарвиса и его части."""
        jarvis = self.settings / JARVIS_DIR
        places = [jarvis]
        for record in previous.values():
            for where in record.get('parts', {}).values():
                path = Path(where)
                if jarvis not in path.parents and path not in places:
                    places.append(path)
        return places

    @staticmethod
    def _names(where: Path) -> set[str]:
        return {child.name for child in where.iterdir()} if where.is_dir() else set()

    def take(self) -> None:
        """Отставить набор в сторону. Настройки харнеса — копией: файл не наш.

        Копия настроек снимается первой, до единого переноса: между первым
        переносом и копией набор уже разобран, а запасного экземпляра настроек
        ещё нет.
        """
        self.store.mkdir(parents=True)
        if self.had_settings:
            self.settings_copy = self.store / SETTINGS_FILE
            shutil.copy2(self.settings_file, self.settings_copy)
        for number, place in enumerate(self.places):
            if not place.exists():
                continue
            backup = self.store / f'{number}-{place.name}'
            os.replace(place, backup)
            self.moved.append((place, backup))

    def restore(self) -> None:
        """Вернуть прежний набор на место и убрать следы неудачной установки."""
        for place, backup in self.moved:
            _remove(place)
            place.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, place)
        if self.settings_copy is not None:
            shutil.copy2(self.settings_copy, self.settings_file)
        elif not self.had_settings:
            # Файла не было до нас — значит, его написала неудачная установка.
            # Был, но копию снять не успели: трогать нечего, он не переезжал.
            self.settings_file.unlink(missing_ok=True)
        for part, names in self.before.items():
            where = self.settings / part
            for name in self._names(where) - names:
                _remove(where / name)
        self.drop()

    def return_built(self) -> None:
        """Вернуть собранное самими модулями: их `bin` установщик не трогает.

        `bin` — не часть модуля, а его собственный склад: там лежит бинарник,
        который модуль собрал себе сам, и отпечаток исходников рядом с ним.
        Отставление уносит его вместе с папкой модуля, и без возврата модуль
        остался бы без инструмента до следующего старта сессии: своим хуком он
        собирается на старте, а после сжатия хуки старта на него не выходят.
        """
        for place, backup in self.moved:
            for built in sorted(backup.glob(f'modules/*/{BUILT_DIR}')):
                target = place / built.relative_to(backup)
                # Модуля в новом наборе нет — возвращать некуда; новый `bin`
                # уже на месте — он свежее нашего.
                if target.exists() or not target.parent.is_dir():
                    continue
                os.replace(built, target)

    def drop(self) -> None:
        shutil.rmtree(self.store, ignore_errors=True)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def run_installer(unpacked: Path, checkout: Path, head: str, source: dict,
                  settings: Path) -> str:
    """Поставить набор из распаковки main теми же каталогами, что у прошлой установки.

    Источником в журнал уходит сам репозиторий с новой головой, а не распаковка:
    распаковки через минуту не будет, а сверять следующей сессии надо с тем, из
    чего набор собран.
    """
    python = source.get('python') or sys.executable
    command = [
        python, str(unpacked.joinpath(*INSTALLER)),
        '--harness', source.get('harness') or HARNESS_DEFAULT,
        '--settings-dir', str(settings),
        '--python', python,
        '--source-path', str(checkout),
        '--source-head', head,
    ]
    if source.get('state_dir'):
        command += ['--state-dir', source['state_dir']]
    try:
        done = subprocess.run(command, capture_output=True, text=True, check=False,
                              timeout=INSTALL_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as failure:
        raise Trouble(f'установщик не запустился: {type(failure).__name__}: {failure}') from failure
    if done.returncode != 0:
        raise Trouble(f'установщик вернул {done.returncode}: {done.stderr.strip()[:300]}')
    return done.stdout


class Module(jarvis.Module):
    """Сверка набора с main и установка, когда он отстал."""

    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        if os.environ.get(ENV_CLOUD) != CLOUD:
            return self._note(runtime, event, NOT_CLOUD, jarvis.Silence(reason=NOT_CLOUD))
        checkout = checkout_of(Path(__file__))
        if checkout is None:
            return self._note(runtime, event, INSTALLED_COPY,
                              jarvis.Silence(reason=INSTALLED_COPY))
        started = time.monotonic()
        settings = settings_dir(os.environ)
        old = None
        try:
            source, previous = installed(settings)
            old = source.get('head')
            head = remote_head(checkout)
            if head == old:
                reason = SAME.format(head=head)
                return self._note(runtime, event, reason, jarvis.Silence(reason=reason),
                                  seconds=time.monotonic() - started)
            return self._catch_up(checkout, settings, source, previous, head,
                                  runtime, event, started)
        except (Trouble, OSError) as failure:
            # Причина не глотается: она и в журнале модуля, и в контексте, где её
            # прочитает человек. Набор при этом цел — прежний, не половинчатый.
            text = FAILED.format(reason=failure, old=old or UNKNOWN_HEAD)
            return self._note(runtime, event, str(failure), jarvis.Context(text),
                              seconds=time.monotonic() - started)

    def _catch_up(self, checkout: Path, settings: Path, source: dict, previous: dict,
                  head: str, runtime, event, started: float) -> jarvis.Response:
        """Донести main, поставить его набор и остановить старт."""
        old = source.get('head')
        unpacked = Path(tempfile.mkdtemp(prefix='env-refresh-'))
        try:
            unpack(checkout, head, unpacked)
            what = changed(checkout, old, head)
            aside = Aside(settings, previous)
            try:
                aside.take()
                run_installer(unpacked, checkout, head, source, settings)
            except BaseException:
                # Установка не дошла до конца: набор возвращается на место
                # целиком, а причина уходит наружу — половинчатого набора
                # после нас не остаётся.
                aside.restore()
                raise
            aside.return_built()
            aside.drop()
        finally:
            shutil.rmtree(unpacked, ignore_errors=True)
        return self._note(
            runtime, event,
            UPDATED.format(old=old or UNKNOWN_HEAD, new=head),
            jarvis.Context(STOP.format(old=_short(old) or UNKNOWN_HEAD, new=_short(head),
                                       changed=what)),
            seconds=time.monotonic() - started,
        )

    @staticmethod
    def _note(runtime, event, reason: str, response, seconds: float | None = None):
        """Журнал модуля в зоне сессии: по строке на ход, с замером времени."""
        entry = {'at': time.time(), 'event': getattr(event, 'event', None),
                 'reason': reason, 'response': response.kind}
        if seconds is not None:
            entry['seconds'] = round(seconds, 3)
        runtime.storage.append_line(JOURNAL, json.dumps(entry, ensure_ascii=False))
        return response


def _short(head: str | None) -> str | None:
    return head[:12] if head else head


if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
