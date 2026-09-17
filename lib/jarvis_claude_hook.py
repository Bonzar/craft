#!/usr/bin/env python3
"""Вход обёртки Claude: один процесс на модуль и событие.

Хук равен процессу, одна обёртка на модуль: падение одного модуля не роняет
остальные, а ответы сводит сам Claude. Установщик регистрирует по строке на
модуль и событие и подставляет в неё абсолютные пути вместо плейсхолдеров.

Запуск (его и пишет установщик):

    jarvis_claude_hook.py --module <slug> --event <единое имя> --install-root <корень>

Файл лежит рядом с пакетом `jarvis` — и в репозитории, и в собранной
библиотеке, — поэтому пакет ищется соседним каталогом.
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis import Module, Storage, install as install_reader, manifest as manifest_reader, run  # noqa: E402
from jarvis.wrappers import claude  # noqa: E402

HOOK_ENTRY = 'module.py'
HOOK_CLASS = 'Module'


def load_module_class(module_dir: Path, slug: str) -> type[Module]:
    """Загрузить класс модуля из его hooks-части."""
    entry = module_dir / 'hooks' / HOOK_ENTRY
    spec = importlib.util.spec_from_file_location(f'jarvis_module_{slug.replace("-", "_")}', entry)
    if spec is None or spec.loader is None:
        raise ImportError(f'модуль {slug}: не читается {entry}')
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    module_class = getattr(loaded, HOOK_CLASS, None)
    if not isinstance(module_class, type) or not issubclass(module_class, Module):
        raise TypeError(f'модуль {slug}: в {entry} нет класса {HOOK_CLASS}, наследника jarvis.Module')
    return module_class


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Обёртка Claude вокруг одного модуля Джарвиса')
    parser.add_argument('--module', required=True, help='slug модуля из шапки')
    parser.add_argument('--event', required=True, help='единое имя события, на которое стоит строка')
    parser.add_argument(
        '--install-root',
        default=str(Path(__file__).resolve().parents[2]),
        help='корень установки (каталог настроек харнеса)',
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, stdin=None, stdout=None, stderr=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    stdin = sys.stdin if stdin is None else stdin
    stderr = sys.stderr if stderr is None else stderr

    raw = json.loads(stdin.read())
    event = claude.to_event(raw)
    if event.event != args.event:
        raise ValueError(
            f'строка зарегистрирована на «{args.event}», а пришло «{event.event}»: '
            'настройки харнеса разошлись с установкой'
        )

    settings_root = Path(args.install_root)
    installed = install_reader.load(settings_root)
    module_dir = installed.module_dir(args.module)
    manifest = manifest_reader.load(module_dir)
    module = load_module_class(module_dir, args.module)()

    storage = Storage(installed.state_dir, event.session_id)
    outcome = run(
        module,
        manifest,
        event,
        storage,
        translate=lambda unified, response: claude.translate(unified, response, slug=manifest.slug),
        install=installed,
        personal_config=installed.personal_config,
        source_config=installed.source_config,
    )
    return claude.emit(outcome.delivery, stdout=stdout, stderr=stderr)


if __name__ == '__main__':
    sys.exit(main())
