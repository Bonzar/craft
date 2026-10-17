"""Хранилище: место, куда модуль пишет и откуда читает состояние.

Даёт его обёртка, реализация у неё. У наших обёрток установленный набор берёт
каталог состояния из журнала установки, переносимый — `~/.local/state/jarvis`.
Харнес про хранилище не знает, модуль сам файл не
выбирает — он называет имя и зону.

Зоны две: постоянная — сам каталог, сессии — подкаталог по идентификатору
сессии из события.
"""

import json
import os
from pathlib import Path

SESSION = 'session'
PERSISTENT = 'persistent'
ZONES = (SESSION, PERSISTENT)

DEFAULT_STATE_DIR = '~/.local/state/jarvis'


def default_state_dir(module_dir: Path | None = None) -> Path:
    """Установленный набор использует единственный путь из журнала установки.

    У переносимого модуля без установщика остаётся штатный домашний путь.
    Среда хука не переопределяет выбранный при установке каталог.
    """
    if module_dir is not None:
        ledger = Path(module_dir).resolve().parent.parent / 'installed.json'
        try:
            record = json.loads(ledger.read_text(encoding='utf-8'))
        except FileNotFoundError:
            record = None
        if record is not None:
            source = record.get('source') if isinstance(record, dict) else None
            state_dir = source.get('state_dir') if isinstance(source, dict) else None
            if not isinstance(state_dir, str) or not state_dir.strip():
                raise ValueError(f'{ledger}: не задан каталог состояния source.state_dir')
            return Path(state_dir).expanduser()
    return Path(DEFAULT_STATE_DIR).expanduser()


class Storage:
    """Хранилище одной сессии: обе зоны за одним объектом."""

    def __init__(self, root: Path | str, session_id: str) -> None:
        if not session_id:
            raise ValueError('хранилищу нужен идентификатор сессии из события')
        self._root = Path(root)
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def persistent_dir(self) -> Path:
        return self._root

    @property
    def session_dir(self) -> Path:
        return self._root / self._session_id

    def dir(self, zone: str = SESSION) -> Path:
        if zone == SESSION:
            return self.session_dir
        if zone == PERSISTENT:
            return self.persistent_dir
        raise ValueError(f'неизвестная зона хранилища: {zone!r}; есть {ZONES}')

    def path(self, name: str, zone: str = SESSION) -> Path:
        """Путь к файлу в зоне. Имя — без разделителей: зону не обойти."""
        if not name or '/' in name or '\\' in name or name in ('.', '..'):
            raise ValueError(f'имя в хранилище должно быть простым: {name!r}')
        return self.dir(zone) / name

    def read_json(self, name: str, zone: str = SESSION, default=None):
        path = self.path(name, zone)
        try:
            text = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return default
        if not text.strip():
            return default
        return json.loads(text)

    def write_json(self, name: str, value, zone: str = SESSION) -> Path:
        """Запись атомарная: временный файл рядом и переименование поверх."""
        path = self.path(name, zone)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f'.tmp-{os.getpid()}')
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(tmp, path)
        return path

    def append_line(self, name: str, line: str, zone: str = SESSION) -> Path:
        """Дописать строку в журнал.

        Открытие на 'a' на POSIX пишет строку одним вызовом в конец файла.
        Целостность журнала при дописывании из многих процессов без лока —
        открытая задача раздела «Библиотека», здесь она не решается.
        """
        path = self.path(name, zone)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(line.rstrip('\n') + '\n')
        return path

    def read_lines(self, name: str, zone: str = SESSION) -> list[str]:
        path = self.path(name, zone)
        try:
            text = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return []
        return [line for line in text.splitlines() if line.strip()]
