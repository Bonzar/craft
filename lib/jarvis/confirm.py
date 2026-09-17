"""Подтверждение: ответ человека на вопрос модуля.

Разрешает одно действие — вызов или окончание хода, — которое без него
запрещено. Харнес подтверждения не запоминает, помним мы: ожидание и выданное
разрешение лежат в зоне сессии хранилища.

Сверка фразой 1:1 идёт на событии «реплика»: текст реплики сравнивается с
ожидаемой фразой целиком, с точностью до обрамляющих пробелов. Частичное
совпадение подтверждением не считается — иначе «не надо, стоп» прошло бы как
«стоп».
"""

from dataclasses import dataclass

from .storage import SESSION, Storage

CONFIRM_FILE = 'confirmations.json'


@dataclass(frozen=True)
class Grant:
    """Выданное подтверждение: один модуль, одно действие, один раз."""

    slug: str
    action: str
    phrase: str


def _load(storage: Storage) -> dict:
    state = storage.read_json(CONFIRM_FILE, zone=SESSION, default=None)
    if not isinstance(state, dict):
        return {'pending': [], 'granted': []}
    state.setdefault('pending', [])
    state.setdefault('granted', [])
    return state


def _save(storage: Storage, state: dict) -> None:
    storage.write_json(CONFIRM_FILE, state, zone=SESSION)


def request(storage: Storage, slug: str, action: str, phrase: str) -> None:
    """Модуль спросил — запоминаем, какой фразы ждём и на какое действие."""
    if not phrase.strip():
        raise ValueError('фраза подтверждения не может быть пустой: сверять будет нечего')
    state = _load(storage)
    pending = [item for item in state['pending'] if not (item['slug'] == slug and item['action'] == action)]
    pending.append({'slug': slug, 'action': action, 'phrase': phrase})
    _save(storage, {'pending': pending, 'granted': state['granted']})


def match_prompt(storage: Storage, prompt_text: str | None, slug: str | None = None) -> list[Grant]:
    """Сверка на событии «реплика». Совпало 1:1 — подтверждение выдано.

    `slug` сужает сверку до ожиданий одного модуля: на событии работает по
    процессу на модуль, и каждый трогает только свои строки.
    """
    if prompt_text is None:
        return []
    spoken = prompt_text.strip()
    if not spoken:
        return []
    state = _load(storage)
    granted, still_pending = list(state['granted']), []
    matched = []
    for item in state['pending']:
        if (slug is None or item['slug'] == slug) and item['phrase'].strip() == spoken:
            matched.append(Grant(slug=item['slug'], action=item['action'], phrase=item['phrase']))
            granted.append(item)
        else:
            still_pending.append(item)
    if matched:
        _save(storage, {'pending': still_pending, 'granted': granted})
    return matched


def take(storage: Storage, slug: str, action: str) -> bool:
    """Израсходовать подтверждение на одно действие. Второй раз его уже нет."""
    state = _load(storage)
    for index, item in enumerate(state['granted']):
        if item['slug'] == slug and item['action'] == action:
            remaining = state['granted'][:index] + state['granted'][index + 1:]
            _save(storage, {'pending': state['pending'], 'granted': remaining})
            return True
    return False


def pending(storage: Storage, slug: str | None = None) -> list[dict]:
    items = _load(storage)['pending']
    return [item for item in items if slug is None or item['slug'] == slug]
