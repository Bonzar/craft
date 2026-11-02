"""Замок craft-type-lock: справочник сущностей и соседи замка."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import CORE_SOURCE, MODULES_DIR
from .craft_type_lock_harness import (
    ENTITY_MANIFEST,
    PARSER,
    SLUG,
    TASK_ADD,
    TASK_TEMPLATES,
    LockHarness,
    entity_body,
    load_lock_module,
    prompt,
    skill,
    write,
)


class EntityFileTest(unittest.TestCase):
    """Модули сущностей находятся по `for`; испорченный data/entity.json — ошибка с путём."""

    def add_module(self, root: Path, slug: str, entity: str | None, serves: bool = True) -> Path:
        folder = root / slug
        (folder / 'data').mkdir(parents=True)
        manifest = ENTITY_MANIFEST.format(slug=slug) if serves else f'slug = "{slug}"\n'
        (folder / 'module.toml').write_text(manifest, encoding='utf-8')
        path = folder / 'data' / 'entity.json'
        if entity is not None:
            path.write_text(entity, encoding='utf-8')
        return path

    def test_the_real_entity_modules_map_every_tag_to_a_module(self) -> None:
        lock = load_lock_module()
        tag_skills = lock.load_tag_skills(MODULES_DIR / SLUG)
        self.assertLessEqual({'craft-task', 'craft-stage', 'craft-section', 'craft-note',
                              'craft-project', 'craft-sphere'},
                             set(tag_skills.values()))
        self.assertEqual(tag_skills['#этап'], 'craft-stage')
        self.assertEqual(tag_skills['#проект'], 'craft-project')
        self.assertEqual(tag_skills['#сфера'], 'craft-sphere')
        entries = lock.load_entities(MODULES_DIR / SLUG)
        self.assertEqual(set(entries), set(tag_skills))
        self.assertTrue(all(entry.templates for entry in entries.values()))
        # Решение не сущность: его модуль замку не адаптер.
        self.assertNotIn('craft-decisions', tag_skills.values())

    def test_a_broken_entity_file_is_an_error(self) -> None:
        lock = load_lock_module()
        template = TASK_TEMPLATES[0]
        bodies = ('{"tags":', '[]', '{"tags": []}', '{"tags": ["задача"]}', '{"tags": [1]}',
                  '{"tags": ["#задача"]}', '{"tags": ["#задача"], "templates": {}}',
                  '{"tags": ["#задача"], "templates": {"#задача": []}}',
                  '{"tags": ["#задача"], "templates": {"#задача": ["шаблон"]}}',
                  json.dumps({'tags': ['#задача'], 'templates': {'#задача': [template], '#веха': [template]}}))
        with tempfile.TemporaryDirectory() as tmp:
            path = self.add_module(Path(tmp), 'craft-x', None)
            for body in bodies:
                path.write_text(body, encoding='utf-8')
                with self.subTest(body=body), self.assertRaises(ValueError) as caught:
                    lock.load_tag_skills(Path(tmp) / SLUG)
                self.assertIn(str(path), str(caught.exception))
            # Один тег у двух модулей — ошибка, а не молчаливый выбор.
            path.write_text(entity_body('#задача'), encoding='utf-8')
            self.add_module(Path(tmp), 'craft-y', entity_body('#Задача'))
            with self.assertRaises(ValueError):
                lock.load_tag_skills(Path(tmp) / SLUG)

    def test_an_adapter_without_an_entity_file_is_an_error(self) -> None:
        lock = load_lock_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.add_module(Path(tmp), 'craft-x', None)
            with self.assertRaises(ValueError) as caught:
                lock.load_tag_skills(Path(tmp) / SLUG)
            self.assertIn(str(path), str(caught.exception))

    def test_an_entity_file_without_for_is_not_the_locks(self) -> None:
        lock = load_lock_module()
        with tempfile.TemporaryDirectory() as tmp:
            self.add_module(Path(tmp), 'craft-task', entity_body('#задача'))
            self.add_module(Path(tmp), 'craft-other', entity_body('#задача'), serves=False)
            self.assertEqual(lock.load_tag_skills(Path(tmp) / SLUG), {'#задача': 'craft-task'})


class BrokenEntityFileTest(LockHarness):
    """Справочник не собирается — запись в Craft не проходит, в причине путь к файлу."""

    def break_entity(self, slug: str, body: str) -> Path:
        path = self.root / 'modules' / slug / 'data' / 'entity.json'
        original = path.read_text(encoding='utf-8')
        self.addCleanup(path.write_text, original, encoding='utf-8')
        path.write_text(body, encoding='utf-8')
        return path

    def test_a_broken_entity_file_denies_the_write_with_its_path(self) -> None:
        path = self.break_entity('craft-note', '{"tags":')
        self.fire(prompt('s-broken-entity'))
        # Вызов скилла без справочника — ошибка хука с путём, скилл не засчитан.
        called = self.run_raw(skill('s-broken-entity', 'craft-task'))
        self.assertNotEqual(called.returncode, 0)
        self.assertIn(str(path), called.stderr)
        result = self.fire(write('s-broken-entity', TASK_ADD))
        self.assert_denied(result, str(path), 'entity.json')

    def test_a_repeated_tag_denies_even_a_write_without_tags(self) -> None:
        tag = json.loads((MODULES_DIR / 'craft-note' / 'data' / 'entity.json')
                         .read_text(encoding='utf-8'))['tags'][0]
        path = self.break_entity('craft-task', entity_body(tag))
        self.fire(prompt('s-repeated-tag'))
        result = self.fire(write('s-repeated-tag', 'blocks add --help'))
        self.assert_denied(result, str(path), 'craft-note')


class MissingNeighboursTest(unittest.TestCase):
    """Без разбора команд замок в стороне; без модулей сущностей требовать нечего."""

    def run_alone(self, root: Path) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, str(root / 'modules' / SLUG / 'hooks' / 'module.py'),
             '--harness', 'claude', '--event', 'pre-tool'],
            input=json.dumps(write('s-missing', TASK_ADD)), text=True,
            capture_output=True, env={'PATH': '/usr/bin:/bin', 'HOME': str(root)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def lay_out(self, root: Path, *neighbours: str) -> None:
        ignore = shutil.ignore_patterns('__pycache__')
        module_dir = root / 'modules' / SLUG
        shutil.copytree(MODULES_DIR / SLUG, module_dir, ignore=ignore)
        shutil.copytree(CORE_SOURCE, module_dir / '_core', ignore=ignore)
        for slug in neighbours:
            shutil.copytree(MODULES_DIR / slug, root / 'modules' / slug, ignore=ignore)

    def test_without_the_parser_the_lock_steps_aside(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.lay_out(Path(tmp))
            output = json.loads(self.run_alone(Path(tmp)).stdout)['hookSpecificOutput']
            self.assertNotIn('permissionDecision', output)
            self.assertIn(PARSER, output['additionalContext'])

    def test_without_entity_modules_a_tagged_write_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.lay_out(Path(tmp), PARSER)
            self.assertEqual(self.run_alone(Path(tmp)).stdout, '')

if __name__ == '__main__':
    unittest.main()
