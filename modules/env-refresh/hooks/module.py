#!/usr/bin/env python3
"""Модуль env-refresh: набор модулей догоняет main на старте облачной сессии.

Зачем. Модули ставит setup-скрипт окружения, а он выполняется один раз — при
сборке снимка окружения; дальше сессии стартуют из снимка, пропуская скрипт
(дока cloud-environments, раздел Environment caching, 19.09.2026). Код
репозитория платформа отдаёт свежим на каждом старте, а набор в домашнем
каталоге остаётся тем, каким его застал снимок: до недели старым. Этот модуль
догоняет его до main.

Только догоняет. Набор трогается ровно в одном случае: коммит, из которого он
поставлен, — предок нынешней головы main, то есть набор просто отстал.
Поставлен он из ветки и предком main не является — это осознанный выбор
человека, и модуль молчит. Журнала установки нет вовсе — окружение не наше,
набор не ставится: приносить его туда, где его нарочно нет, модуль не вправе.

Где он работает. Только в облачной сессии Claude Code on the web: кеш
окружения — её особенность. Без признака `CLAUDE_CODE_REMOTE` модуль молчит. В
автономном прогоне тоже молчит: остановка старта имеет смысл там, где её есть
кому исполнить, а рутина по ней встанет и работу не сделает.

Две роли одного файла.

Копия из чекаута — запускатель. Её зовёт строка хука из `.claude/settings.json`
самого репозитория, она приезжает с чекаутом и потому свежая по построению. Она
сверяет набор с main, доносит объекты и разворачивает main рядом — и на этом её
работа кончается.

Копия из распаковки — работник. Установку делает она, своим же установщиком из
того же коммита: договор между модулем и установщиком (флаги, форма журнала)
живёт в одном коммите, и стороны обязаны браться оттуда же. Иначе выходит то,
что поймала живая проба 19.09.2026: модуль с ветки позвал установщик из main
флагами, которых в main ещё нет, и установщик отказал на разборе аргументов.
Копии модуля в распаковке нет — передавать работу некому: одна строка в
контекст, ничего не тронуто.

Что не трогается. Рабочее дерево сессии, её HEAD и ветка: ни checkout, ни
reset, ни смены ветки. `git fetch` доносит объекты в хранилище клона, а сам
набор ставится из распаковки main рядом.

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

import argparse
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
# Установленная копия везёт ядро внутри себя; у исходника в чекауте и в
# распаковке своего ядра нет — там оно общее, в `core/` репозитория.
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
# Где в распаковке лежит работник — копия этого же файла из main.
WORKER = ('modules', 'env-refresh', 'hooks', 'module.py')
# Флаг работника. Argparse обёртки его не знает, поэтому разбирается он до неё,
# в точке входа.
WORK_FLAG = '--install'
HARNESS_DEFAULT = 'claude'

GIT = 'git'
ORIGIN = 'origin'
MAIN_REF = 'refs/heads/main'
# Пределы у каждого шага свои и все вместе заведомо меньше срока хука: хук,
# убитый харнесом посреди установки, вернуть набор на место уже не успеет.
LS_REMOTE_TIMEOUT_SEC = 120
FETCH_TIMEOUT_SEC = 600
INSTALL_TIMEOUT_SEC = 600
# `git merge-base --is-ancestor` отвечает кодом: 0 — предок, 1 — не предок,
# остальное — не ответил вовсе (в мелком клоне коммита может не быть).
ANCESTOR = 0
NOT_ANCESTOR = 1

JOURNAL = 'env-refresh.jsonl'
NOT_CLOUD = 'не облачная сессия'
AUTONOMOUS = 'автономный прогон: набор рутины без человека не трогаем'
INSTALLED_COPY = 'запуск не из чекаута: набор догоняет копия из чекаута'
NO_LEDGER = 'журнала установки нет: набор в этом окружении ставили не мы'
NO_SOURCE = 'журнал не назвал коммит источника: чем набор поставлен, неизвестно'
SAME = 'набор уже стоит по main {head}'
OWN_CHOICE = 'набор поставлен из {old} — это не предок main {new}, не трогаем'
UNCLEAR = 'предок ли {old} для main {new}, узнать не вышло: {reason}'
UPDATED = 'набор обновлён с {old} на {new}'
NO_WORKER = ('в main нет модуля свежести ({where}): передать работу некому, '
             'а ставить набор чужим кодом модуль не станет')

REPORT = (
    'Набор модулей агента обновлён с {old} на {new} — {changed}. Старт продолжается: '
    'правила, скиллы и данные этого старта харнес читает уже после обновления '
    '(замер 19.09.2026, claude 2.1.278), сжимать контекст не нужно.'
)
FAILED = (
    'Набор модулей не удалось догнать до main: {reason}. Работаем прежним набором '
    '(источник {old}) — он цел.'
)
UNKNOWN_HEAD = 'коммит не назван'
CHANGED_UNKNOWN = 'состав изменений не сравнить: прошлого коммита в клоне нет'


class Trouble(Exception):
    """Причина, по которой набор остался прежним. Текст идёт человеку как есть."""


def reason_of(text: str) -> str:
    """Причина отказа из вывода команды: последняя содержательная строка.

    Последняя, а не первые знаки: у argparse и у git самое главное стоит в
    конце, а обрезание по знакам рвёт фразу посреди слова — человек видит
    «error: un» и не знает ничего.
    """
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    return lines[-1] if lines else 'причину команда не назвала'


def git(where: Path, *args: str, timeout: int) -> str:
    """Вызов git. Не вышло — Trouble с причиной, а не пустой ответ."""
    try:
        done = subprocess.run(
            [GIT, '-C', str(where), *args],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
    except FileNotFoundError as failure:
        raise Trouble('в окружении нет git') from failure
    except (OSError, subprocess.SubprocessError) as failure:
        raise Trouble(f'git {args[0]} не отработал: {type(failure).__name__}: {failure}') from failure
    if done.returncode != 0:
        raise Trouble(f'git {args[0]} вернул {done.returncode}: {reason_of(done.stderr)}')
    return done.stdout


def settings_dir(env) -> Path:
    """Каталог настроек идущего харнеса."""
    return Path(env.get(ENV_SETTINGS) or SETTINGS_DEFAULT).expanduser()


def installed(settings: Path) -> tuple[dict, dict] | None:
    """Журнал прошлой установки: чем набор поставлен и что в нём стоит.

    Журнала нет — None: набор в этом окружении ставили не мы, и приносить его
    туда, где его нарочно нет, модуль не вправе.
    """
    try:
        text = settings.joinpath(*LEDGER).read_text(encoding='utf-8')
    except OSError:
        return None
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


# --- запускатель: сверить, донести, передать работу ---


def checkout_of(entry: Path) -> Path | None:
    """Чекаут репозитория, из которого запущен этот файл. Не из чекаута — None.

    Признак — установщик на своём месте над папкой модуля: только рядом с ним
    лежит и код, которым ставить, и модули, которые ставить.
    """
    root = entry.resolve().parents[3]
    return root if root.joinpath(*INSTALLER).is_file() else None


def remote_head(checkout: Path) -> str:
    """Голова main у origin. Один запрос, объекты не качаются."""
    out = git(checkout, 'ls-remote', ORIGIN, MAIN_REF, timeout=LS_REMOTE_TIMEOUT_SEC)
    first = out.split('\n', 1)[0].split('\t', 1)[0].strip()
    if not first:
        raise Trouble(f'у origin нет ветки main (ссылка {MAIN_REF})')
    return first


def fetch(checkout: Path) -> None:
    """Донести объекты main в хранилище клона. Рабочее дерево не трогается."""
    git(checkout, 'fetch', '--quiet', ORIGIN, MAIN_REF, timeout=FETCH_TIMEOUT_SEC)


def behind_main(checkout: Path, old: str, head: str) -> tuple[bool, str]:
    """Отстал ли набор: предок ли его коммит нынешней голове main.

    Предок — набор просто отстал, догоняем. Не предок — набор поставлен из
    ветки нарочно, и стирать его нельзя. Ответа нет вовсе (клон мелкий, коммита
    в нём нет) — считаем, что не отстал: трогаем только то, про что доказано,
    что оно отстало.
    """
    try:
        done = subprocess.run(
            [GIT, '-C', str(checkout), 'merge-base', '--is-ancestor', old, head],
            capture_output=True, text=True, check=False, timeout=LS_REMOTE_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError) as failure:
        return False, UNCLEAR.format(old=_short(old), new=_short(head),
                                     reason=f'{type(failure).__name__}: {failure}')
    if done.returncode == ANCESTOR:
        return True, ''
    if done.returncode == NOT_ANCESTOR:
        return False, OWN_CHOICE.format(old=_short(old), new=_short(head))
    return False, UNCLEAR.format(old=_short(old), new=_short(head),
                                 reason=reason_of(done.stderr))


def unpack(checkout: Path, head: str, into: Path) -> None:
    """Развернуть main рядом, не трогая рабочее дерево."""
    archive = into / 'main.tar'
    git(checkout, 'archive', '--format=tar', '-o', str(archive), head,
        timeout=FETCH_TIMEOUT_SEC)
    with tarfile.open(archive) as bundle:
        bundle.extractall(into, filter='data')
    archive.unlink()


def hand_off(unpacked: Path, checkout: Path, head: str, python: str) -> str:
    """Отдать установку копии модуля из распаковки и вернуть её ответ.

    Работник ставит набор своим установщиком из того же коммита. Наружу он
    отдаёт готовую строку для модели, а причину отказа — в stderr.
    """
    worker = unpacked.joinpath(*WORKER)
    if not worker.is_file():
        raise Trouble(NO_WORKER.format(where='/'.join(WORKER)))
    command = [python, str(worker), WORK_FLAG, '--checkout', str(checkout), '--head', head]
    try:
        done = subprocess.run(command, capture_output=True, text=True, check=False,
                              timeout=INSTALL_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as failure:
        raise Trouble(f'работник не запустился: {type(failure).__name__}: {failure}') from failure
    if done.returncode != 0:
        raise Trouble(f'работник вернул {done.returncode}: {reason_of(done.stderr)}')
    return done.stdout.strip()


class Module(jarvis.Module):
    """Запускатель: сверка с main и передача установки работнику из распаковки."""

    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        if os.environ.get(ENV_CLOUD) != CLOUD:
            return self._note(runtime, event, NOT_CLOUD, jarvis.Silence(reason=NOT_CLOUD))
        if runtime.autonomous:
            return self._note(runtime, event, AUTONOMOUS, jarvis.Silence(reason=AUTONOMOUS))
        checkout = checkout_of(Path(__file__))
        if checkout is None:
            return self._note(runtime, event, INSTALLED_COPY,
                              jarvis.Silence(reason=INSTALLED_COPY))
        started = time.monotonic()
        settings = settings_dir(os.environ)
        old = None
        try:
            ledger = installed(settings)
            if ledger is None:
                return self._quiet(runtime, event, NO_LEDGER, started)
            source, _ = ledger
            old = source.get('head')
            head = remote_head(checkout)
            if head == old:
                return self._quiet(runtime, event, SAME.format(head=head), started)
            if not old:
                return self._quiet(runtime, event, NO_SOURCE, started)
            fetch(checkout)
            behind, why = behind_main(checkout, old, head)
            if not behind:
                return self._quiet(runtime, event, why, started)
            return self._catch_up(checkout, source, old, head, runtime, event, started)
        except (Trouble, OSError) as failure:
            # Причина не глотается: она и в журнале модуля, и в контексте, где её
            # прочитает человек. Набор при этом цел — прежний, не половинчатый.
            text = FAILED.format(reason=failure, old=_short(old) or UNKNOWN_HEAD)
            return self._note(runtime, event, str(failure), jarvis.Context(text),
                              seconds=time.monotonic() - started)

    def _catch_up(self, checkout: Path, source: dict, old: str, head: str,
                  runtime, event, started: float) -> jarvis.Response:
        """Развернуть main рядом и отдать установку работнику из распаковки."""
        unpacked = Path(tempfile.mkdtemp(prefix='env-refresh-'))
        try:
            unpack(checkout, head, unpacked)
            said = hand_off(unpacked, checkout, head, source.get('python') or sys.executable)
        finally:
            shutil.rmtree(unpacked, ignore_errors=True)
        return self._note(
            runtime, event,
            UPDATED.format(old=_short(old), new=_short(head)),
            jarvis.Context(said) if said else jarvis.Silence(),
            seconds=time.monotonic() - started,
        )

    def _quiet(self, runtime, event, reason: str, started: float) -> jarvis.Response:
        """Набор не трогаем и в контекст не говорим: причина живёт в журнале."""
        return self._note(runtime, event, reason, jarvis.Silence(reason=reason),
                          seconds=time.monotonic() - started)

    @staticmethod
    def _note(runtime, event, reason: str, response, seconds: float | None = None):
        """Журнал модуля в зоне сессии: по строке на ход, с замером времени."""
        entry = {'at': time.time(), 'event': getattr(event, 'event', None),
                 'reason': reason, 'response': response.kind}
        if seconds is not None:
            entry['seconds'] = round(seconds, 3)
        runtime.storage.append_line(JOURNAL, json.dumps(entry, ensure_ascii=False))
        return response


# --- работник: поставить набор своим установщиком ---


class Aside:
    """Прежний набор, отставленный в сторону на время установки.

    Отставляется переименованием, а не копией: набор весит мегабайты, а рядом с
    ним лежит собранное модулями. Не дошла установка — набор возвращается на
    место целиком; дошла — отставленное удаляется, а собранное возвращается.
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
        jarvis_dir = self.settings / JARVIS_DIR
        places = [jarvis_dir]
        for record in previous.values():
            for where in record.get('parts', {}).values():
                path = Path(where)
                if jarvis_dir not in path.parents and path not in places:
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
                  settings: Path) -> None:
    """Поставить набор теми же каталогами, что у прошлой установки.

    Установщик берётся из своей распаковки: он и работник — один коммит, и
    договор между ними не может разъехаться. Источником в журнал уходит сам
    репозиторий с новой головой, а не распаковка: распаковки через минуту не
    будет, а сверять следующей сессии надо с тем, из чего набор собран.
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
        raise Trouble(f'установщик вернул {done.returncode}: {reason_of(done.stderr)}')


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


def work(checkout: Path, head: str) -> int:
    """Установка по поручению запускателя. Печатает строку остановки.

    Работник живёт в распаковке main: и установщик, и форма журнала берутся из
    того же коммита, что и он сам.
    """
    unpacked = Path(__file__).resolve().parents[3]
    settings = settings_dir(os.environ)
    ledger = installed(settings)
    if ledger is None:
        raise Trouble(NO_LEDGER)
    source, previous = ledger
    old = source.get('head')
    what = changed(checkout, old, head)
    aside = Aside(settings, previous)
    try:
        aside.take()
        run_installer(unpacked, checkout, head, source, settings)
    except BaseException:
        # Установка не дошла до конца: набор возвращается на место целиком, а
        # причина уходит наружу — половинчатого набора после нас не остаётся.
        aside.restore()
        raise
    aside.return_built()
    aside.drop()
    print(REPORT.format(old=_short(old) or UNKNOWN_HEAD, new=_short(head), changed=what))
    return 0


def _short(head: str | None) -> str | None:
    return head[:12] if head else head


def main(argv: list[str]) -> int:
    """Работник: поручение от запускателя, а не событие харнеса."""
    parser = argparse.ArgumentParser(description='Установка набора из распаковки main')
    parser.add_argument(WORK_FLAG, action='store_true', required=True,
                        help='поставить набор по поручению запускателя')
    parser.add_argument('--checkout', required=True, help='чекаут репозитория сессии')
    parser.add_argument('--head', required=True, help='коммит main, из которого ставим')
    args = parser.parse_args(argv)
    try:
        return work(Path(args.checkout), args.head)
    except (Trouble, OSError) as failure:
        # Причину читает запускатель последней строкой stderr и несёт её в
        # контекст как есть.
        print(str(failure), file=sys.stderr)
        return 1


if __name__ == '__main__':
    if WORK_FLAG in sys.argv[1:]:
        sys.exit(main(sys.argv[1:]))
    sys.exit(wrappers.run_hook(__file__, Module))
