"""Модуль и его ход на событии.

Логика в модуле и библиотеке, обёртка только переводит. Всё, что одинаково у
всех модулей, живёт здесь: чтение режима, поиск requires, сверка подтверждения
на реплике, запись следа. Модуль пишет только `handle`.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from . import confirm, mode, registry
from .autonomy import is_autonomous
from .event import Event
from .events import PROMPT
from .manifest import Manifest, resolve
from .response import Context, Response, Silence
from .storage import Storage
from .trace import Trace, TraceLine

MODE_OFF_REASON = 'модуль выключен'


@dataclass(frozen=True)
class Delivery:
    """Перевод ответа в форму харнеса — то, что обёртка напечатает."""

    payload: dict | None = None
    exit_code: int = 0
    supported: bool = True
    note: str | None = None


@dataclass(frozen=True)
class Runtime:
    """Что библиотека даёт модулю на событии."""

    storage: Storage
    manifest: Manifest
    module_dir: Path
    autonomous: bool
    requires: Mapping[str, list[Manifest]]

    def library(self, requirement: str):
        """Библиотечный модуль по требованию из requires. Нет такого — None."""
        return registry.find(requirement, self.module_dir)

    def ask_confirmation(self, action: str, phrase: str) -> None:
        confirm.request(self.storage, self.manifest.slug, action, phrase)

    def take_confirmation(self, action: str) -> bool:
        return confirm.take(self.storage, self.manifest.slug, action)


@dataclass(frozen=True)
class Outcome:
    """Итог хода модуля на одном событии."""

    response: Response
    delivery: Delivery
    trace_line: TraceLine


class Module:
    """База модуля. Молчание — законный ответ и состояние по умолчанию."""

    def handle(self, event: Event, runtime: Runtime) -> Response:
        return Silence()


def _reason_of(response: Response) -> str | None:
    for field_name in ('reason', 'text'):
        value = getattr(response, field_name, None)
        if isinstance(value, str) and value:
            return value
    return None


def _requires_gap(manifest: Manifest, event: Event, module_dir: Path):
    """Чего модулю не хватает из requires. Хватает всего — None.

    Модуль, у которого на событии не нашлось ничего из requires, говорит об
    этом в чате и молчит: называет, чего нет, и своего хода не делает.
    """
    found, missing = resolve(manifest.requires, registry.neighbours(module_dir))
    if not missing:
        return None, found
    listed = ', '.join(missing)
    text = (
        f'Модуль «{manifest.slug}» на событии «{event.event}» не нашёл ничего '
        f'из требуемого: {listed}. Пока этого нет, модуль молчит.'
    )
    return (Context(text), f'не найдено из requires: {listed}'), found


class _Recorder:
    """Перевод ответа и запись следа: у одного события — ровно одна строка."""

    def __init__(self, trace: Trace, translate, event: Event, module: Module, manifest: Manifest,
                 decision, autonomous: bool) -> None:
        self._trace = trace
        self._translate = translate
        self._event = event
        self._common = dict(
            event=event.event,
            module=manifest.slug,
            module_class=type(module).__name__,
            mode_enabled=decision.enabled,
            mode_source=decision.source,
            autonomous=autonomous,
        )

    def finish(self, response: Response, reason: str | None) -> Outcome:
        delivery = self._translate(self._event.event, response)
        line = self._trace.write(
            response=response.kind,
            reason=reason,
            delivered=delivery.supported,
            not_delivered_reason=delivery.note,
            **self._common,
        )
        return Outcome(response=response, delivery=delivery, trace_line=line)

    def crashed(self, error: BaseException) -> None:
        """Ошибка не глотается: в след с контекстом, и наружу её же."""
        self._trace.write(
            response='error',
            reason=f'{type(error).__name__}: {error}',
            delivered=False,
            not_delivered_reason='модуль упал на событии',
            **self._common,
        )


def run(
    module: Module,
    manifest: Manifest,
    event: Event,
    storage: Storage,
    translate: Callable[[str, Response], Delivery],
    module_dir: Path,
    personal_config: Path | None = None,
    source_config: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Outcome:
    """Провести модуль по событию и записать след — ровно одной строкой."""
    autonomous = is_autonomous(env)
    decision = mode.read(manifest.slug, storage, personal_config, source_config)
    recorder = _Recorder(Trace(storage), translate, event, module, manifest, decision, autonomous)

    if not decision.enabled:
        return recorder.finish(Silence(), MODE_OFF_REASON)

    gap, found = _requires_gap(manifest, event, module_dir)
    if gap is not None:
        return recorder.finish(*gap)

    if event.event == PROMPT:
        confirm.match_prompt(storage, event.prompt_text, slug=manifest.slug)

    runtime = Runtime(
        storage=storage,
        manifest=manifest,
        module_dir=Path(module_dir),
        autonomous=autonomous,
        requires=found,
    )
    try:
        response = module.handle(event, runtime)
        if not isinstance(response, Response):
            raise TypeError(
                f'модуль {manifest.slug} вернул {type(response).__name__}, '
                'а не форму единого ответа'
            )
    except Exception as error:
        # И падение внутри handle, и ответ не той формы — одинаково событие без
        # ответа: строка следа пишется до проброса, иначе по следу это
        # неотличимо от хука, который вовсе не запускался.
        recorder.crashed(error)
        raise

    return recorder.finish(response, _reason_of(response))
