"""Хранилище ревью хода: объём, состояние и вердикт в зоне сессии.

Пакет из модулей по предмету, а здесь только их публичные имена: поставщики
и хуки зовут `review.KIND_WRITE`, `review.append_turn_record` и остальное
отсюда, не зная, в каком модуле что лежит.

- records — записи объёма по видам и фабрики для поставщиков.
- volume — файл объёма: разбор, починка, счёт, отпечаток.
- state — состояние ревью, его файл и лок.
- turns — номер хода у записи и реплики, очистка объёма.
- verdict — проверка и приём вердикта подагента.
- criteria — критерий, правило applies и проблемы приёма.
- brief — сборка поручения подагенту.
- question — вопрос самого ревью о застрявшем ревью.

Подмодули грузятся относительным импортом, поэтому lib грузится пакетом:
ядро делает это само (registry.LibraryModule), скрипт отпечатка — так же,
с путём поиска подмодулей.
"""

from .brief import (
    BRIEF_FILE, BriefError, BriefParts, CriteriaCheck, assemble_brief, providers_problem,
    review_criteria, volume_skills, write_brief,
)
from .criteria import (
    Always, Craft, Criterion, CriterionId, Rule, Tags, criteria_problem, criterion_applies,
    is_tag, make_criterion, parse_applies,
)
from .question import OPTION_RELEASE, OPTION_REVIEW, is_review_question, review_question
from .records import (
    KIND_ANCHOR, KIND_ANSWER, KIND_KEY, KIND_PROMPT, KIND_READ, KIND_SKILL, KIND_WRITE, KINDS,
    AnchorRecord, AnswerRecord, PromptRecord, ReadRecord, SkillRecord, VolumeRecord, WriteRecord,
    make_anchor_record, make_answer_record, make_prompt_record, make_read_record,
    make_skill_record, make_write_record, record_row,
)
from .state import (
    NO_BRIEF, STATE_FILE, BriefCriteria, ReviewState, VolumeId, current_turn, load_state,
    new_volume_id, peek_state, save_state, state_lock,
)
from .turns import append_turn_record, claim_prompt_turn, clear_volume
from .verdict import (
    CHECKED_KEY, CLEAN_KEY, CRITERION_KEY, FINGERPRINT_KEY, REMARKS_KEY, TEXT_KEY, VERDICT_FILE,
    WHERE_KEY, drop_verdict, peek_verdict, read_verdict, verdict_key,
)
from .volume import (
    FINGERPRINT_CHARS, VOLUME_FILE, Fingerprint, count_kind, count_writes, fingerprint,
    load_volume, parse_volume_file, peek_volume, writes_before_turn,
)

__all__ = [
    'BRIEF_FILE', 'CHECKED_KEY', 'CLEAN_KEY', 'CRITERION_KEY', 'FINGERPRINT_CHARS',
    'FINGERPRINT_KEY', 'KINDS', 'KIND_ANCHOR', 'KIND_ANSWER', 'KIND_KEY', 'KIND_PROMPT',
    'KIND_READ', 'KIND_SKILL', 'KIND_WRITE', 'NO_BRIEF', 'OPTION_RELEASE', 'OPTION_REVIEW',
    'REMARKS_KEY', 'STATE_FILE', 'TEXT_KEY', 'VERDICT_FILE', 'VOLUME_FILE', 'WHERE_KEY',
    'Always', 'AnchorRecord', 'AnswerRecord', 'BriefCriteria', 'BriefError', 'BriefParts',
    'Craft', 'CriteriaCheck', 'Criterion', 'CriterionId', 'Fingerprint', 'PromptRecord',
    'ReadRecord', 'ReviewState', 'Rule', 'SkillRecord', 'Tags', 'VolumeId', 'VolumeRecord',
    'WriteRecord', 'append_turn_record', 'assemble_brief', 'claim_prompt_turn', 'clear_volume',
    'count_kind', 'count_writes', 'criteria_problem', 'criterion_applies', 'current_turn',
    'drop_verdict', 'fingerprint', 'is_review_question', 'is_tag', 'load_state', 'load_volume',
    'make_anchor_record', 'make_answer_record', 'make_criterion', 'make_prompt_record',
    'make_read_record', 'make_skill_record', 'make_write_record', 'new_volume_id',
    'parse_applies', 'parse_volume_file', 'peek_state', 'peek_verdict', 'peek_volume',
    'providers_problem', 'read_verdict', 'record_row', 'review_criteria', 'review_question',
    'save_state', 'state_lock', 'verdict_key', 'volume_skills', 'write_brief',
    'writes_before_turn',
]
