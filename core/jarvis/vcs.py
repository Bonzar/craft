"""Коммиты в папку одним видом для git и arc.

Свежесть набора сверяется не по голове репозитория, а по коммитам в его файлы:
коммит мимо набора ничего в нём не меняет, а транк Аркадии движется каждую
секунду. Здесь то, что для этого нужно у обеих систем: последний коммит в
папку на ревизии, голова удалённой главной ветки, изменились ли файлы папки
между двумя ревизиями и отстала ли установленная ревизия от удалённой.

Чем системы различаются. В git главная ветка — main у origin, и отставание —
это предок: коммит из ветки предком main не станет, его ставили нарочно. В
Аркадии trunk линейный, а ветки вливаются в него склейкой: коммит ветки не
бывает предком trunk, даже влитый. Поэтому у arc отставание — это коммит trunk
в папку новее установленного.

Замер arc 27.10.2026 на VFS-маунте: `arc fetch trunk` ~0,15 с (тянет только
ссылку), `arc log -n1` по папке ~0,2 с, `arc diff --name-only` ~0,1 с. Замер git
там же: `git ls-remote` к GitHub по ssh 2–3 с.

Ошибка любой команды — VcsError с причиной из её вывода, а не пустой ответ:
«не смогли проверить» и «нечего сообщать» снаружи должны различаться.
"""

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

COMMAND_TIMEOUT_SEC = 30
FETCH_TIMEOUT_SEC = 120
GIT_ORIGIN = 'origin'
GIT_MAIN_REF = 'refs/heads/main'
ARC_TRUNK = 'arcadia/trunk'
# `git merge-base --is-ancestor` отвечает кодом: 0 — предок, 1 — не предок,
# остальное — не ответил вовсе (в мелком клоне коммита может не быть).
ANCESTOR = 0
NOT_ANCESTOR = 1
FIELD = '\t'


class VcsError(Exception):
    """Команда не ответила. Текст — причина для человека."""


@dataclass(frozen=True)
class Version:
    """Последний коммит в папку: хеш и время коммита в ISO 8601."""

    commit: str
    date: str

    def label(self) -> str:
        return f'{self.commit[:12]} от {self.date[:10]}'


def last_line(text: str) -> str:
    """Причина отказа: последняя содержательная строка вывода.

    У argparse, git и arc главное стоит в конце, а обрезание по знакам рвёт
    фразу посреди слова.
    """
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    return lines[-1] if lines else 'причину команда не назвала'


def run(command: list[str], where: Path, timeout: int = COMMAND_TIMEOUT_SEC,
        allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    """Запуск команды в папке. Не вышло — VcsError с причиной."""
    # Хук старта сессии не может ответить на вопрос пароля: без терминала git
    # ждал бы его до таймаута.
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    try:
        done = subprocess.run(command, cwd=where, capture_output=True, text=True,
                              check=False, timeout=timeout, env=env)
    except FileNotFoundError as failure:
        raise VcsError(f'в окружении нет {command[0]}') from failure
    except (OSError, subprocess.SubprocessError) as failure:
        raise VcsError(f'{command[0]} {command[1]} не отработал: '
                       f'{type(failure).__name__}: {failure}') from failure
    if done.returncode not in allowed:
        raise VcsError(f'{command[0]} {command[1]} вернул {done.returncode}: '
                       f'{last_line(done.stderr)}')
    return done


def parse_version(out: str) -> Version | None:
    first = out.strip().split('\n', 1)[0]
    if not first:
        return None
    commit, _, date = first.partition(FIELD)
    return Version(commit.strip(), date.strip())


class Git:
    """Набор в git-репозитории; удалённое — main у origin."""

    remote_name = 'main'

    def __init__(self, where: Path, paths: tuple[str, ...] = ('.',)) -> None:
        self.where = where
        self.paths = paths

    def _git(self, *args: str, **options) -> subprocess.CompletedProcess:
        return run(['git', *args], self.where, **options)

    def version(self, rev: str) -> Version | None:
        out = self._git('log', '-1', f'--format=%H{FIELD}%cI', rev, '--', *self.paths).stdout
        return parse_version(out)

    def remote(self) -> str:
        """Голова main у origin. Объекты доносятся, только если их в клоне нет."""
        out = self._git('ls-remote', GIT_ORIGIN, GIT_MAIN_REF).stdout
        head = out.split('\n', 1)[0].split(FIELD, 1)[0].strip()
        if not head:
            raise VcsError(f'у origin нет ветки main (ссылка {GIT_MAIN_REF})')
        present = self._git('cat-file', '-e', f'{head}^{{commit}}', allowed=(0, 1, 128))
        if present.returncode != 0:
            self._git('fetch', '--quiet', GIT_ORIGIN, GIT_MAIN_REF, timeout=FETCH_TIMEOUT_SEC)
        return head

    def changed(self, old: str, new: str) -> list[str]:
        # `-z`: имена не экранируются, иначе путь с кириллицей придёт в кавычках.
        out = self._git('diff', '--name-only', '-z', old, new, '--', *self.paths).stdout
        return [name for name in out.split('\0') if name]

    def behind(self, old: str, new: str, installed: Version | None,
               latest: Version | None) -> bool | None:
        """Отстал ли old: предок ли он new. Не ответил git — None."""
        done = self._git('merge-base', '--is-ancestor', old, new,
                         allowed=(ANCESTOR, NOT_ANCESTOR, 128))
        if done.returncode == ANCESTOR:
            return True
        if done.returncode == NOT_ANCESTOR:
            return False
        return None


class Arc:
    """Модуль в смонтированной Аркадии; удалённое — trunk."""

    remote_name = 'trunk'

    def __init__(self, where: Path, paths: tuple[str, ...] = ('.',)) -> None:
        self.where = where
        self.paths = paths

    def _arc(self, *args: str, **options) -> subprocess.CompletedProcess:
        return run(['arc', *args], self.where, **options)

    def version(self, rev: str) -> Version | None:
        out = self._arc('log', '-n1', f'--format={{commit}}{FIELD}{{date}}', rev,
                        '--', *self.paths).stdout
        return parse_version(out)

    def remote(self) -> str:
        """Голова trunk. `arc fetch` обновляет только ссылку, файлы не трогает."""
        self._arc('fetch', 'trunk', timeout=FETCH_TIMEOUT_SEC)
        return self._arc('rev-parse', ARC_TRUNK).stdout.strip()

    def changed(self, old: str, new: str) -> list[str]:
        out = self._arc('diff', '--name-only', old, new, '--', *self.paths).stdout
        return [name for name in out.splitlines() if name.strip()]

    def behind(self, old: str, new: str, installed: Version | None,
               latest: Version | None) -> bool | None:
        """Отстал ли установленный: коммит trunk в папку новее установленного."""
        if installed is None or latest is None:
            return None
        try:
            return datetime.fromisoformat(latest.date) > datetime.fromisoformat(installed.date)
        except ValueError:
            return None


def detect(where: Path, paths: tuple[str, ...] = ('.',)) -> Git | Arc | None:
    """Чем версионирована папка. Ни git, ни arc — None."""
    if not where.is_dir():
        return None
    for kind, probe in ((Git, ['git', 'rev-parse', '--show-toplevel']), (Arc, ['arc', 'info'])):
        try:
            run(probe, where)
        except VcsError:
            continue
        return kind(where, paths)
    return None


def folder_commit(where: Path) -> str | None:
    """Последний коммит в папку на текущей ревизии. Не под версиями — None."""
    found = detect(where)
    if found is None:
        return None
    try:
        version = found.version('HEAD')
    except VcsError:
        return None
    return version.commit if version else None
