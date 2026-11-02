"""Поручение подагенту на остановке: каркас с диска, сборка, файл и текст блока.

Сборка и текст — функции lib, здесь только то, что знает хук: где лежат
каркас и скрипт отпечатка и куда хранилище кладёт файлы сессии.
"""

from pathlib import Path

from review_access import sources

MODULE_DIR = Path(__file__).resolve().parents[1]
FRAME_FILE = MODULE_DIR / 'data' / 'review-frame.md'
# Описания видов записей объёма: формат строк задаёт только контракт ревью.
RECORDS_FILE = MODULE_DIR / 'data' / 'review-records.md'
# Подагент считает отпечаток скриптом, хук — функцией из lib: функция одна.
FINGERPRINT_SCRIPT = MODULE_DIR / 'hooks' / 'fingerprint.py'


def read_own(review, path: Path, what: str) -> str:
    """Свой файл данных. Не читается — BriefError с путём, а не падение хука."""
    try:
        return path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as error:
        raise review.BriefError(f'{what} {path} не читается ({error})') from error


def nudge_text(storage, state, runtime, review, volume: list,
               problem: str | None) -> tuple[str, tuple[str, ...]]:
    """Блок с просьбой запустить подагента и список критериев собранного поручения.

    Поручение собирается здесь из каркаса, описаний видов записей и критериев,
    чьё правило applies совпало с объёмом: always — всегда, craft — при
    записях write, tags — когда запись несёт тег из правила. Оно кладётся в
    хранилище сессии. Не разобранные файлы stop называет в problem. Каркас не
    прочитать или поручение не записать — BriefError.
    """
    parts = review.assemble_brief(sources(runtime, review), volume,
                                  read_own(review, FRAME_FILE, 'каркас поручения'),
                                  read_own(review, RECORDS_FILE, 'описания записей'))
    brief = review.write_brief(storage, parts.text)
    volume_file = storage.path(review.VOLUME_FILE).resolve()
    facts = review.NudgeFacts(
        brief=str(brief), volume_file=str(volume_file),
        verdict_file=str(storage.path(review.VERDICT_FILE).resolve()),
        volume_id=state.volume_id,
        fingerprint_command=f'python3 {FINGERPRINT_SCRIPT} {volume_file}',
        writes=review.count_writes(volume), fingerprint=review.fingerprint(volume),
        criteria=parts.criteria)
    return review.nudge_text(facts, problem), parts.criteria
