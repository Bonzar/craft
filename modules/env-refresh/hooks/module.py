#!/usr/bin/env python3
"""Модуль env-refresh: сессия узнаёт, что её набор модулей устарел.

Зачем. Набор модулей ставится один раз, а репозиторий, из которого он
поставлен, меняется дальше. Сессия на старом наборе работает по старым правилам
и скиллам, а человек не знает, что окружение надо обновить.

База — установленная копия, в любом харнесе и окружении. Она сверяет набор с
удалённым по коммитам в его файлы (`freshness.py`) и, если набор отстал,
говорит в контекст, что окружение надо обновить, с версиями. Ничего не ставит и
не меняет. Проверить не вышло — говорит и это, с причиной. Набор из ветки и
модуль, которого на удалённом нет, — выбор человека, тут база молчит.

Адаптер облачного Claude — копия из чекаута. В облаке снимок окружения отстаёт
от main до недели (дока cloud-environments, раздел Environment caching,
19.09.2026), и набор там не предупреждает, а догоняет main сам: адаптер берёт
вердикт базы и, если набор craft отстал, ставит его заново. Её зовёт строка хука
в `.claude/settings.json` репозитория: она приезжает с чекаутом и потому свежая
по построению. База в облаке при живом адаптере молчит, чтобы не говорить
дважды. Вне облака копия из чекаута молчит: предупреждает установленная.

Две роли адаптера. Копия из чекаута — запускатель: сверяет, доносит объекты и
разворачивает main рядом. Копия из распаковки — работник: ставит набор своим же
установщиком из того же коммита, иначе договор между модулем и установщиком
(флаги, форма журнала) разъезжается — это поймала живая проба 19.09.2026.

Что не трогается. Рабочее дерево сессии, её HEAD и ветка. Провал установки
возвращает прежний набор на место целиком.
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
# Сверка живёт рядом, в той же части hooks.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import jarvis  # noqa: E402
from jarvis import vcs, wrappers  # noqa: E402

import freshness  # noqa: E402

# Признак облачной сессии Claude Code on the web.
ENV_CLOUD = 'CLAUDE_CODE_REMOTE'
CLOUD = 'true'
# Корень проекта сессии Claude: в облаке там чекаут с адаптером.
ENV_PROJECT = 'CLAUDE_PROJECT_DIR'
# Каталог настроек харнеса задаёт сам харнес, а не прошлая установка: ставим
# туда, откуда идущая сессия читает настройки.
ENV_SETTINGS = 'CLAUDE_CONFIG_DIR'
SETTINGS_DEFAULT = '~/.claude'
LEDGER = ('jarvis', 'installed.json')
LEDGER_FILE = 'installed.json'
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

# Пределы у каждого шага свои и все вместе заведомо меньше срока хука: хук,
# убитый харнесом посреди установки, вернуть набор на место уже не успеет.
ARCHIVE_TIMEOUT_SEC = 600
INSTALL_TIMEOUT_SEC = 600

JOURNAL = 'env-refresh.jsonl'
AUTONOMOUS = 'автономный прогон: набор рутины без человека не трогаем'
DEFER = 'облако с адаптером в чекауте: набор догоняет он, база молчит'
LOCAL_CHECKOUT = 'не облако: предупреждает установленная копия, копия из чекаута молчит'
NO_LEDGER = 'журнала установки нет: набор в этом окружении ставили не мы'
UPDATED = 'набор обновлён с {old} на {new}'
NO_WORKER = ('в main нет модуля свежести ({where}): передать работу некому, '
             'а ставить набор чужим кодом модуль не станет')

REPORT = (
    'Набор модулей агента обновлён с {old} на {new} — {changed}. Старт продолжается: '
    'правила и скиллы этого старта харнес читает уже после обновления (замер '
    '19.09.2026, claude 2.1.278). Соседние хуки старта шли параллельно с обновлением: '
    'что они собрали до подмены, могло уехать в контекст из прежнего набора.'
)
FAILED = (
    'Набор модулей не удалось догнать до main: {reason}. Работаем прежним набором '
    '(источник {old}) — он цел.'
)
UNKNOWN_HEAD = 'коммит не назван'
CHANGED_UNKNOWN = 'состав изменений не сравнить: прошлого коммита в клоне нет'


class Trouble(Exception):
    """Причина, по которой набор остался прежним. Текст идёт человеку как есть."""


def settings_dir(env) -> Path:
    """Каталог настроек идущего харнеса."""
    return Path(env.get(ENV_SETTINGS) or SETTINGS_DEFAULT).expanduser()


def installed(settings: Path) -> tuple[dict, dict] | None:
    """Журнал прошлой установки в каталоге настроек. Нет журнала — None."""
    try:
        return freshness.read_ledger(settings.joinpath(*LEDGER))
    except freshness.LedgerError as failure:
        raise Trouble(str(failure)) from failure


def checkout_of(entry: Path) -> Path | None:
    """Чекаут репозитория, из которого запущен этот файл. Не из чекаута — None.

    Признак — установщик на своём месте над папкой модуля: только рядом с ним
    лежит и код, которым ставить, и модули, которые ставить.
    """
    root = entry.resolve().parents[3]
    return root if root.joinpath(*INSTALLER).is_file() else None


def adapter_in_charge(env) -> bool:
    """Облачный Claude с адаптером в чекауте проекта: набор догоняет он."""
    project = env.get(ENV_PROJECT)
    return (env.get(ENV_CLOUD) == CLOUD and bool(project)
            and Path(project).joinpath(*WORKER).is_file()
            and Path(project).joinpath(*INSTALLER).is_file())


def unpack(checkout: Path, head: str, into: Path) -> None:
    """Развернуть main рядом, не трогая рабочее дерево."""
    archive = into / 'main.tar'
    try:
        vcs.run(['git', 'archive', '--format=tar', '-o', str(archive), head], checkout,
                timeout=ARCHIVE_TIMEOUT_SEC)
    except vcs.VcsError as failure:
        raise Trouble(str(failure)) from failure
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
        raise Trouble(f'работник вернул {done.returncode}: {vcs.last_line(done.stderr)}')
    return done.stdout.strip()


class Module(jarvis.Module):
    """База в установленной копии, адаптер облачного Claude в копии из чекаута."""

    def handle(self, event: jarvis.Event, runtime: jarvis.Runtime) -> jarvis.Response:
        if runtime.autonomous:
            return self._note(runtime, event, AUTONOMOUS, jarvis.Silence(reason=AUTONOMOUS))
        checkout = checkout_of(Path(__file__))
        if checkout is not None:
            if os.environ.get(ENV_CLOUD) != CLOUD:
                return self._note(runtime, event, LOCAL_CHECKOUT,
                                  jarvis.Silence(reason=LOCAL_CHECKOUT))
            return self._adapt(checkout, runtime, event)
        if adapter_in_charge(os.environ):
            return self._note(runtime, event, DEFER, jarvis.Silence(reason=DEFER))
        return self._warn(runtime, event)

    def _warn(self, runtime, event) -> jarvis.Response:
        """База: сверить и сказать, ничего не меняя."""
        started = time.monotonic()
        # Журнал лежит над каталогом модулей той установки, что запустила эту
        # копию: так база находит его в любом харнесе, не зная его настроек.
        ledger_path = Path(__file__).resolve().parents[3] / LEDGER_FILE
        try:
            ledger = freshness.read_ledger(ledger_path)
        except freshness.LedgerError as failure:
            text = freshness.UNKNOWN_HEAD.format(items=str(failure))
            return self._note(runtime, event, str(failure), jarvis.Context(text),
                              seconds=time.monotonic() - started)
        if ledger is None:
            return self._note(runtime, event, NO_LEDGER, jarvis.Silence(reason=NO_LEDGER),
                              seconds=time.monotonic() - started)
        findings = freshness.check_all(*ledger)
        return self._say(runtime, event, findings, freshness.warning(findings), started)

    def _adapt(self, checkout: Path, runtime, event) -> jarvis.Response:
        """Адаптер облака: вердикт базы, и если набор craft отстал — поставить заново."""
        started = time.monotonic()
        old = None
        try:
            ledger = installed(settings_dir(os.environ))
            if ledger is None:
                return self._note(runtime, event, NO_LEDGER, jarvis.Silence(reason=NO_LEDGER),
                                  seconds=time.monotonic() - started)
            source, modules = ledger
            old = source.get('head')
            main, *rest = freshness.check_all(source, modules, where=checkout)
            if main.state != freshness.STALE:
                findings = [main, *rest]
                return self._say(runtime, event, findings, freshness.warning(findings), started)
            said = self._catch_up(checkout, source, main.new)
            text = ' '.join(part for part in (said, freshness.warning(rest)) if part)
            return self._say(runtime, event, [main, *rest], text, started,
                             reason=UPDATED.format(old=_short(old), new=_short(main.new)))
        except (Trouble, OSError) as failure:
            # Причина не глотается: она и в журнале модуля, и в контексте, где её
            # прочитает человек. Набор при этом цел — прежний, не половинчатый.
            text = FAILED.format(reason=failure, old=_short(old) or UNKNOWN_HEAD)
            return self._note(runtime, event, str(failure), jarvis.Context(text),
                              seconds=time.monotonic() - started)

    @staticmethod
    def _catch_up(checkout: Path, source: dict, head: str) -> str:
        """Развернуть main рядом и отдать установку работнику из распаковки."""
        unpacked = Path(tempfile.mkdtemp(prefix='env-refresh-'))
        try:
            unpack(checkout, head, unpacked)
            return hand_off(unpacked, checkout, head, source.get('python') or sys.executable)
        finally:
            shutil.rmtree(unpacked, ignore_errors=True)

    def _say(self, runtime, event, findings, text: str, started: float,
             reason: str | None = None) -> jarvis.Response:
        """Сказать в контекст, если есть что; сверка — в журнал модуля всегда."""
        reason = reason or freshness.journal(findings)
        response = jarvis.Context(text) if text else jarvis.Silence(reason=reason)
        return self._note(runtime, event, reason, response, seconds=time.monotonic() - started)

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
    for extra in source.get('extra') or []:
        command += ['--extra-modules', extra]
    try:
        done = subprocess.run(command, capture_output=True, text=True, check=False,
                              timeout=INSTALL_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as failure:
        raise Trouble(f'установщик не запустился: {type(failure).__name__}: {failure}') from failure
    if done.returncode != 0:
        raise Trouble(f'установщик вернул {done.returncode}: {vcs.last_line(done.stderr)}')


def changed(checkout: Path, old: str | None, new: str) -> str:
    """Чем новый набор отличается от прежнего: модули и прочие каталоги источника."""
    if not old:
        return CHANGED_UNKNOWN
    try:
        names = vcs.Git(checkout).changed(old, new)
    except vcs.VcsError:
        return CHANGED_UNKNOWN
    return freshness.describe(names) or 'файлы набора не изменились'


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
