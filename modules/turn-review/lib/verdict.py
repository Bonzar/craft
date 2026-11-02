"""Вердикт подагента: проверка формы, приём на остановке и превью до неё.

Вердикт пишет подагент, хук только читает его и убирает. Принимается он по
критериям поручения и отпечатку записей write, которые проверил подагент.
"""

from .criteria import CriterionId
from .state import VolumeId
from .volume import Fingerprint

VERDICT_FILE = 'turn-review-verdict.json'

VOLUME_ID_KEY = 'volume_id'
CLEAN_KEY = 'clean'
REMARKS_KEY = 'remarks'
CHECKED_KEY = 'checked'
FINGERPRINT_KEY = 'fingerprint'
CRITERION_KEY = 'criterion'
WHERE_KEY = 'where'
TEXT_KEY = 'text'
REMARK_FIELDS = (CRITERION_KEY, WHERE_KEY, TEXT_KEY)


def drop_verdict(storage) -> None:
    storage.path(VERDICT_FILE).unlink(missing_ok=True)


def _remark_problem(remark, criteria: tuple[CriterionId, ...]) -> str | None:
    if not isinstance(remark, dict):
        return f'замечание {remark!r} не объект'
    for field_name in REMARK_FIELDS:
        if not isinstance(remark.get(field_name), str) or not remark[field_name].strip():
            return f'у замечания нет поля «{field_name}» строкой'
    if remark[CRITERION_KEY] not in criteria:
        return f'критерий «{remark[CRITERION_KEY]}» не из {", ".join(criteria)}'
    return None


def _checked_problem(checked: list, clean: bool, criteria: tuple[CriterionId, ...]) -> str | None:
    unknown = [str(item) for item in checked if item not in criteria]
    if unknown:
        return f'в «{CHECKED_KEY}» критерии не из {", ".join(criteria)}: {", ".join(unknown)}'
    missing = [item for item in criteria if item not in checked]
    if clean and missing:
        return f'clean=true, а критерии не пройдены: {", ".join(missing)}'
    return None


def _fingerprint_problem(verdict: dict, expected: Fingerprint) -> str | None:
    checked = verdict.get(FINGERPRINT_KEY)
    if not isinstance(checked, str) or not checked.strip():
        return f'поле «{FINGERPRINT_KEY}» не строка'
    if checked.strip() != expected:
        return (f'отпечаток вердикта {checked.strip()} не сходится с объёмом {expected}: '
                'объём менялся после ревью')
    return None


def verdict_problem(verdict: dict, expected: Fingerprint,
                    criteria: tuple[CriterionId, ...]) -> str | None:
    """Что не так с вердиктом нашего объёма по критериям поручения. Всё по форме — None."""
    clean, remarks, checked = (verdict.get(CLEAN_KEY), verdict.get(REMARKS_KEY),
                               verdict.get(CHECKED_KEY))
    if not isinstance(clean, bool):
        return f'поле «{CLEAN_KEY}» не true и не false'
    if not isinstance(remarks, list):
        return f'поле «{REMARKS_KEY}» не список'
    if not isinstance(checked, list):
        return f'поле «{CHECKED_KEY}» не список'
    for remark in remarks:
        problem = _remark_problem(remark, criteria)
        if problem:
            return problem
    if clean and remarks:
        return 'clean=true при замечаниях'
    if not clean and not remarks:
        return 'clean=false без замечаний'
    return _checked_problem(checked, clean, criteria) or _fingerprint_problem(verdict, expected)


def read_verdict(storage, volume_id: VolumeId, expected: Fingerprint,
                 criteria: tuple[CriterionId, ...]) -> tuple[dict | None, str | None]:
    """Вердикт нашего объёма и что с ним не так.

    Чужой id — вердикт по старому объёму: его не видно, как будто его нет.
    Вердикт не по форме или по меньшему объёму убирается, а его изъян идёт в
    поручение: иначе испорченный файл держал бы ход до конца сессии.
    """
    path = storage.path(VERDICT_FILE)
    try:
        verdict = storage.read_json(VERDICT_FILE, default=None)
    except ValueError as error:
        drop_verdict(storage)
        return None, f'вердикт {path} не JSON ({error})'
    if not isinstance(verdict, dict) or verdict.get(VOLUME_ID_KEY) != volume_id:
        return None, None
    problem = verdict_problem(verdict, expected, criteria)
    if problem:
        drop_verdict(storage)
        return None, f'вердикт {path} не принят: {problem}'
    return verdict, None


def verdict_key(verdict: dict) -> str:
    """Чем вердикт отличается от другого: id объёма и отпечаток проверенных записей."""
    return f'{verdict[VOLUME_ID_KEY]}:{verdict[FINGERPRINT_KEY]}'


def peek_verdict(storage, volume_id: VolumeId, criteria: tuple[CriterionId, ...],
                 problem: str | None) -> dict | None:
    """Вердикт нашего объёма до stop, для превью. Файл не трогается.

    Отпечаток с объёмом не сверяется: это превью, принимает вердикт stop.
    Критерии те же, по которым его примет stop, а не `checked` самого
    вердикта: иначе чистый вердикт с пустым `checked` открыл бы ответ.
    Чистый вердикт при проблеме критериев (`problem` из review_criteria) —
    None, как на stop. Не JSON или не по форме — None: изъян назовёт stop.
    """
    try:
        verdict = storage.read_json(VERDICT_FILE, default=None)
    except ValueError:
        return None
    if not isinstance(verdict, dict) or verdict.get(VOLUME_ID_KEY) != volume_id:
        return None
    expected = verdict.get(FINGERPRINT_KEY)
    expected = Fingerprint(expected.strip() if isinstance(expected, str) else '')
    if verdict_problem(verdict, expected, criteria):
        return None
    return None if verdict[CLEAN_KEY] and problem else verdict
