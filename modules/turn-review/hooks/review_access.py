"""Своя lib ревью из хука и то, что ей нужно от рантайма: каталог модулей.

Точка входа хука — hooks/module.py: она ставит ядро в путь поиска до
импорта этого файла.
"""

from pathlib import Path

from jarvis import registry


def load_review(runtime):
    """Своя lib-часть: хранилище ревью и имена полей."""
    found = registry.library_of(runtime.manifest)
    if found is None:
        raise RuntimeError(f'у модуля {runtime.manifest.slug} нет части lib: хранилищу ревью '
                           'взяться неоткуда. Переустанови набор из свежего источника')
    return found.load()


def modules_dir(runtime) -> Path:
    return registry.modules_dir(runtime.module_dir)


def required_criteria(state, runtime, review, volume: list):
    """Критерии, по которым принимается вердикт, и почему чистый вердикт не принять.

    Критерии и criteria_problem у stop и превью общие: одна функция lib.
    Нет ли поставщика (providers_problem), проверяет только stop.
    """
    return review.review_criteria(modules_dir(runtime), volume, state.criteria)
