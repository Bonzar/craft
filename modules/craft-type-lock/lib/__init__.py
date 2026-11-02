"""Поиск тегов типа для соседей: тег → скилл и теги в команде записи.

Код живёт в точке входа замка, а здесь только отдаётся соседям по slug. Так
источник один: правило «тег в тексте ссылки не считается» и разбор `\\uXXXX`
меняются в одном месте и сразу у всех, кто читает записи по типу. Справочник
«тег → модуль» собирается по файлам data/entity.json модулей сущностей.
"""

import importlib.util
from pathlib import Path

HOOK_FILE = Path(__file__).resolve().parents[1] / 'hooks' / 'module.py'


def _load_hook():
    """Точка входа замка как модуль, без запуска: `__main__` у неё не наш."""
    spec = importlib.util.spec_from_file_location(f'{__name__}.hook', HOOK_FILE)
    if spec is None or spec.loader is None:
        raise ImportError(f'точка входа замка не импортируется из {HOOK_FILE}')
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


_hook = _load_hook()

ENTITY_FILE = _hook.ENTITY_FILE


def load_tag_skills(modules_dir: Path | None = None) -> dict[str, str]:
    """Тег → slug модуля сущности по файлам data/entity.json соседей."""
    return _hook.load_tag_skills(modules_dir)


def find_tags(command: str, tag_skills: dict[str, str]) -> list[str]:
    """Теги типа в команде записи; теги в тексте ссылок на блок не считаются."""
    return _hook.find_tags(command, _hook.tag_pattern(tag_skills))


def skill_slug(tool_input) -> str:
    """Slug скилла из входа инструмента Skill; префикс плагина отрезан."""
    return _hook.skill_slug(tool_input)
