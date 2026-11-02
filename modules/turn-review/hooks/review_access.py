"""Своя lib ревью из хука и то, что ей нужно от рантайма: поставщики.

Точка входа хука — hooks/module.py: она ставит ядро в путь поиска до
импорта этого файла.
"""

from jarvis import registry


def load_review(runtime):
    """Своя lib-часть: хранилище ревью и имена полей."""
    found = registry.library_of(runtime.manifest)
    if found is None:
        raise RuntimeError(f'у модуля {runtime.manifest.slug} нет части lib: хранилищу ревью '
                           'взяться неоткуда. Переустанови набор из свежего источника')
    return found.load()


SOURCES_PROBLEM = ('поставщиков ревью не прочитать: шапка соседнего модуля не разбирается '
                   '({error}). Ревью не знает, кто копит объём и привозит критерии, ход не '
                   'отпущен. Скажи об этом человеку: починить или переустановить модуль')


class SourcesError(Exception):
    """Шапки соседних модулей не прочитать: ни поставщиков, ни критериев."""


def sources(runtime, review):
    """Поставщики ревью — модули, чей `for` называет turn-review.

    Испорченная шапка соседа (ValueError ядра) — SourcesError с причиной:
    падение хука отпустило бы ход без ревью.
    """
    try:
        found = registry.adapters(runtime.module_dir, review.REVIEW_SLUG)
    except ValueError as error:
        raise SourcesError(SOURCES_PROBLEM.format(error=error)) from error
    providers = tuple(review.Provider(manifest.slug, manifest.path, bool(manifest.events))
                      for manifest in found if manifest.path is not None)
    return review.Sources(registry.modules_dir(runtime.module_dir), providers)


def required_criteria(state, runtime, review, volume: list):
    """Критерии, по которым принимается вердикт, и почему чистый вердикт не принять.

    Критерии и criteria_problem у stop и превью общие: одна функция lib.
    Нет ли поставщика (providers_problem), проверяет только stop.
    """
    return review.review_criteria(sources(runtime, review), volume, state.criteria)
