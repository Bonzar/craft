"""Render source qualifications and observation dates for memory overviews."""
from __future__ import annotations

import re
import json
from typing import TYPE_CHECKING

from .capture import declared_kind
from .model import ModelError, ModelRequest, format_prompt
from .storage import _timestamp

if TYPE_CHECKING:
    from .engine import ViewSpec

_MAX_MODEL_EVIDENCE_CHARACTERS = 60_000
PROJECTION_PROTOCOL = "compact-overview-v6:source-message-clock-v1"

_CURRENT_STATE = re.compile(r"\b(?:сейчас|на сегодня|на текущий момент|в данный момент|currently|right now)\b", re.IGNORECASE)


def _include_observation_date(query: str, summary: str, evidence: list[dict]) -> str:
    if not _CURRENT_STATE.search(query):
        return summary
    observations = [row for row in evidence if row["disposition"] != "superseded" and (
        declared_kind(row["text"], row["metadata"]) == "observation" or row.get("exception_basis") == "observation"
        or row.get("conflict_basis") == "reported_observation"
    )]
    notes = []
    for row in observations:
        if row.get("valid_at"):
            notes.append(f"Датированное наблюдение [{row['id']}] относится к {row['valid_at']}.")
        else:
            notes.append(f"Запись о наблюдении [{row['id']}] получена {row['recorded_at']}. Время наблюдения неизвестно.")
    if notes:
        note = "Даты исходных наблюдений; их соответствие объекту вопроса нужно проверить по тексту:\n" + "\n".join(notes)
    else:
        note = "В выбранных источниках нет наблюдения за состоянием."
    return summary + "\n\n" + note + " Состояние прямо сейчас по этим данным не подтверждено."


def _include_required_evidence(
    summary: str, evidence: list[dict], links: list[dict], *, include_records: bool = True,
) -> str:
    proposals = {record["id"] for record in evidence if record["disposition"] == "proposal"}
    scoped_changes = {
        (link["from_id"], link["to_id"], link["metadata"]["condition"])
        for link in links if link["kind"] in {"exception_to", "supersedes", "depends_on"}
        and not proposals.intersection((link["from_id"], link["to_id"]))
        and isinstance(link["metadata"].get("condition"), str) and link["metadata"]["condition"]
    }
    lines = [
        f"- Условие для [{source}] относительно [{target}]: {condition}"
        for source, target, condition in sorted(scoped_changes)
    ]
    if include_records:
        required = {key for source, target, _ in scoped_changes for key in (source, target)}
        labels = {"exception": "Исключение", "conflict": "Открытый спор"}
        for record in evidence:
            disposition = record["disposition"]
            if disposition in labels or (record["id"] in required and disposition != "superseded"):
                label = labels.get(disposition, "Основание")
                if disposition == "exception" and record.get("exception_basis") == "observation":
                    label = "Наблюдаемое исключение"
                lines.append(f"- {label} [{record['id']}]: {record['text']}")
    if lines:
        heading = "Условия, исключения и споры из исходных записей" if include_records else "Условия из исходных записей"
        return summary + "\n\n" + heading + "\n" + "\n".join(lines)
    return summary




def _model_record(record: dict) -> dict:
    """Project interpreted facts for generation; preserve full proof in the pinned snapshot."""
    fields = ('id','text','actor','source','scope','valid_at','recorded_at','disposition',
              'exception_basis','conflict_basis')
    result = {key:record[key] for key in fields if key in record}
    metadata = record.get('metadata',{})
    kept = ('kind','role','source_method','reporting_status','temporal_precision',
            'source_recorded_at','source_available_at','source_message_at','source_message_clock_basis','timezone')
    proof = {key:metadata[key] for key in kept if key in metadata}
    if isinstance(metadata.get('interpretation'),dict):
        proof['interpretation']={key:metadata['interpretation'][key] for key in ('status',)
                                 if key in metadata['interpretation']}
        coverage=metadata['interpretation'].get('source_coverage')
        if isinstance(coverage,dict):
            # Coverage critique is audit/feedback, never additional domain evidence.
            proof['interpretation']['source_coverage']={key:coverage[key] for key in ('status','protocol')
                                                       if key in coverage}
    result['metadata']=proof
    revalidation=record.get('current_source_revalidation')
    if isinstance(revalidation,dict):
        result['current_source_revalidation']={key:revalidation[key] for key in (
            'event_id','source_run_id','source_status','protocol','checked_at',
            'last_positive_claim_run_id','last_positive_source_status') if key in revalidation}
    if 'branch_review' in record:
        result['branch_review']={key:value for key,value in record['branch_review'].items()
                                if key in ('view_id','reviewer','recorded_at','status','decision')}
    return result


def _model_reference(reference: dict) -> dict:
    fields=('event_id','start','end','role','relation')
    return {key:reference[key] for key in fields if key in reference}


def _model_edge(edge: dict) -> dict:
    # Relation recording time is pinned audit, not event/effective time. Model
    # qualification coverage needs one identity, endpoints, relation and condition.
    fields=('from_id','to_id','kind')
    result={key:edge[key] for key in fields if key in edge}
    if edge.get('id') is not None or edge.get('edge_id') is not None:
        result['id']=edge.get('id',edge.get('edge_id'))
    metadata=edge.get('metadata',{})
    result['metadata']={key:metadata[key] for key in ('condition','basis','origin') if key in metadata}
    if isinstance(metadata.get('verification'),dict):
        result['metadata']['verification']={key:metadata['verification'][key] for key in ('status',)
                                            if key in metadata['verification']}
    return result


def _request(
    spec: ViewSpec, evidence: list[dict], links: list[dict], missing: list[str], *, partial: bool = False,
    valid_as_of: str | None = None, recorded_as_of: str | None = None,
    max_characters: int = _MAX_MODEL_EVIDENCE_CHARACTERS,
) -> ModelRequest:
    active = [_model_record(item) for item in evidence if item["disposition"] != "superseded"]
    instructions = (
        "Записи содержат уже интерпретированные утверждения с атрибуцией, а не журнал работы агента. "
        "Аудит происхождения хранится отдельно; его отсутствие здесь не означает отсутствие сведений в источниках. "
        "Кратко ответь по-русски. Различай предложения, решения, сообщения о реализации, наблюдения и выводы. "
        "Учитывай ограничения и исключения, помечай пробелы и конфликты. "
        "Сохраняй отношения включения, одновременности и порядка, условия и принадлежность описанному эпизоду. "
        "Не складывай пересекающиеся интервалы и повторные включения одного источника без оснований. "
        "Не превращай условную закономерность в факт отдельного эпизода; неизвестные границы оставляй неизвестными. "
        "interpretation.source_coverage описывает исходную проверку полноты при создании утверждения. "
        "current_source_revalidation отдельно отражает проверку состава смыслов источника на границе этой выборки: "
        "текущий accepted_complete учитывай вместо устаревшего unresolved исходной проверки. "
        "Текущий unresolved или failed не подтверждает полноту, но не отменяет положительно проверенные факты; "
        "last_positive_claim_run_id и last_positive_source_status указывают их прежнюю положительную аттестацию. "
        "Эти ID, protocol и checked_at — аудит, не предметные факты, ссылки-основания, даты событий или новые наблюдения. "
        "Проверка полноты источника не доказывает внешнюю истинность или свежее состояние мира. "
        "Связь с предложением сама по себе не меняет правило. exception_basis=observation описывает проверку, не решение. "
        "Ссылайся на ID оснований. Для каждого вывода о состоянии укажи объект и дату наблюдения. "
        "Проверяй, относится ли наблюдение к объекту вопроса: новая запись о другом объекте не подтверждает его состояние. "
        "Подтверждение результата на одну дату не доказывает состояние сейчас. "
        "Если оснований именно для запрошенного объекта, события или даты нет, явно скажи, что сведений недостаточно. "
        f"Связи между записями: {_json([_model_edge(link) for link in links])}. Недоступные начальные записи: {_json(missing)}."
    )
    cutoffs = {"valid_as_of": _timestamp(valid_as_of) if valid_as_of else None,
               "recorded_as_of": _timestamp(recorded_as_of) if recorded_as_of else None}
    instructions += (" Временная перспектива запроса " + _json(cutoffs) + ". "
                     "При заданном valid_as_of описывай состояние на этот момент. "
                     "recorded_as_of ограничивает сведения, которыми могла располагать база к этому моменту.")
    if partial:
        instructions += (
            " Это один из пакетов большого обзора. Ответь только по этому пакету. "
            "Отсутствие сведений в нём не означает отсутствие сведений в остальных пакетах; "
            "не делай общего вывода о полноте ответа или отсутствии фактов."
        )
    if any("branch_review" in item for item in active):
        instructions += " branch_review подтверждает релевантность записи для этой ветки. Сохраняй её исходный статус."
    request = ModelRequest(spec.query, active, instructions, usage_task='Generate memory answer')
    if len(format_prompt(request)) > max_characters:
        raise ModelError("too much linked evidence for one model request; use complete semantic batches")
    return request


def _request_batches(
    spec: ViewSpec, evidence: list[dict], links: list[dict], missing: list[str], *,
    valid_as_of: str | None = None, recorded_as_of: str | None = None,
    max_characters: int = _MAX_MODEL_EVIDENCE_CHARACTERS, family_check=None,
):
    """Pack complete restriction closures, repeating shared prerequisites when needed."""
    from .engine import _mandatory_adjacency, _reachable
    cutoffs = dict(valid_as_of=valid_as_of, recorded_as_of=recorded_as_of, max_characters=max_characters)
    def check(records, edges, partial=False):
        _request(spec, records, edges, missing, partial=partial, **cutoffs)
        if family_check is not None:
            family_check(records, edges, partial)
    try:
        check(evidence, links)
        return [(evidence, links)]
    except ModelError:
        pass
    adjacency = _mandatory_adjacency(links)
    records = {row["id"]: row for row in evidence}
    positions = {row["id"]: index for index, row in enumerate(evidence)}
    remaining = set(records)
    components = []
    while remaining:
        ids, _ = _reachable({min(remaining)}, adjacency)
        ids &= records.keys()
        remaining.difference_update(ids)
        component = [records[key] for key in sorted(ids, key=positions.__getitem__)]
        component_links = [edge for edge in links if ids.intersection((edge["from_id"], edge["to_id"]))]
        try:
            check(component, component_links, True)
        except ModelError:
            # Entail each bounded part with complete incident conditions. Other endpoint
            # bodies remain explicit boundary references for composition/global review.
            for record in component:
                incident = [edge for edge in links if record['id'] in (edge['from_id'], edge['to_id'])]
                try:
                    check([record], incident, True)
                except ModelError as boundary_error:
                    raise ModelError('mandatory evidence component exceeds request budget: '+
                        ', '.join(sorted(ids))+'; complete boundary qualifications cannot fit for '+record['id']) from boundary_error
                components.append(([record], incident))
            continue
        components.append((component, component_links))
    batches = []
    current, current_links = [], []
    for component, component_links in components:
        proposed = list({row['id']: row for row in [*current, *component]}.values())
        batch_links = list({edge['id']: edge for edge in [*current_links, *component_links]}.values())
        try:
            check(proposed, batch_links, True)
        except ModelError:
            if current:
                batches.append((current, current_links))
            current, current_links = component, component_links
        else:
            current, current_links = proposed, batch_links
    if current:
        batches.append((current, current_links))
    return batches



def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
