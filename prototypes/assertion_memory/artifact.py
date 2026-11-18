"""Export an overview with pinned evidence and actionable review items."""

from __future__ import annotations

from .engine import ViewVersion


_LABELS = {
    "decision": "Решение", "observation": "Наблюдение", "proposal": "Предложение",
    "exception": "Исключение", "conflict": "Открытый спор", "superseded": "Прежняя формулировка",
    "implementation": "Сообщение о реализации", "inference": "Вывод",
}


def _quote(value: str) -> str:
    return "\n".join("> " + line for line in value.splitlines())


def render_artifact(title: str, version: ViewVersion) -> str:
    parts = [f"# {' '.join(title.split())}", f"Снимок {version.id}, собран {version.created_at}."]
    if version.valid_as_of:
        parts.append(f"Время действия до {version.valid_as_of} включительно.")
    if version.recorded_as_of:
        parts.append(f"Сведения, полученные до {version.recorded_as_of} включительно.")
    parts.extend(("## Описание", version.summary))
    review = [f"- {label} [{row['id']}]" for row in version.evidence
              if (label := _LABELS.get(row["disposition"])) in {"Предложение", "Открытый спор"}]
    review.extend(f"- Нет основания [{key}]" for key in version.missing_roots)
    if not version.evidence:
        review.append("- В выбранном срезе нет сведений для описания.")
    parts.extend(("## На ревью", "\n".join(review) if review else "В снимке нет открытых вопросов с отдельными записями."))
    proposals = {row["id"] for row in version.evidence if row["disposition"] == "proposal"}
    for link in version.edges:
        condition = link["metadata"].get("condition")
        if link.get("kind") in {"exception_to", "supersedes"} and isinstance(condition, str) and condition:
            kind = "предложения" if proposals.intersection((link["from_id"], link["to_id"])) else "связи"
            parts.append(f"Условие {kind} [{link['from_id']}] → [{link['to_id']}]\n\n" + _quote(condition))
    if version.evidence:
        parts.append("## Исходные записи")
        for row in version.evidence:
            label = _LABELS.get(row["disposition"], "Утверждение")
            if row.get("exception_basis") == "observation" and row["disposition"] == "exception":
                label = "Наблюдаемое исключение"
            origin = f"{row['actor']} · {row['source']} · {row['recorded_at']}"
            parts.append(f"**{label} [{row['id']}]**\n\n{_quote(row['text'])}\n\n{_quote(origin)}")
            excerpt = row["metadata"].get("excerpt")
            if isinstance(excerpt, str) and excerpt and excerpt != row["text"]:
                parts.append("Фрагмент источника\n\n" + _quote(excerpt))
    return "\n\n".join(parts)
