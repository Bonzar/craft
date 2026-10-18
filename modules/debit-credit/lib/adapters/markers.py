"""Предложенный формат меток. Чтение не меняет пользовательские заметки."""

import re
from dataclasses import asdict, dataclass
from urllib.parse import quote, unquote

TOKEN_PATTERN = re.compile(r'[a-z0-9][a-z0-9-]*')
MARKER_PATTERN = re.compile(r'\[dc-sync:v1 source=([^\s\]]+) id=([^\s\]]+)(?: role=([^\s\]]+))?\]')


@dataclass(frozen=True)
class Marker:
    source: str
    id: str
    role: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, str) or not TOKEN_PATTERN.fullmatch(self.source):
            raise ValueError('Некорректный источник метки')
        if not isinstance(self.id, str) or not self.id:
            raise ValueError('Пустой идентификатор метки')
        if self.role is not None and not TOKEN_PATTERN.fullmatch(self.role):
            raise ValueError('Некорректная роль метки')

    def render(self) -> str:
        role = f' role={self.role}' if self.role else ''
        return f'[dc-sync:v1 source={self.source} id={quote(self.id, safe="")}{role}]'

    def as_dict(self) -> dict:
        return asdict(self)


def extract_markers(notes: str) -> list[Marker]:
    matches = list(MARKER_PATTERN.finditer(notes))
    if len(matches) != notes.count('[dc-sync:'):
        raise ValueError('Некорректная или неизвестная версия служебной метки')
    result = []
    for match in matches:
        encoded = match[2]
        if re.search(r'%(?![0-9A-Fa-f]{2})', encoded):
            raise ValueError('Некорректное кодирование идентификатора метки')
        result.append(Marker(match[1], unquote(encoded, errors='strict'), match[3]))
    return result
