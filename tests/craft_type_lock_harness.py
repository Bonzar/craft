"""Замок craft-type-lock: общая обвязка тестов — хук подпроцессом, поддельный connect-API."""

import http.server
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

from jarvis import registry
from jarvis.storage import Storage
from jarvis.trace import Trace

from . import CORE_SOURCE, MODULES_DIR

SLUG = 'craft-type-lock'
# Модули сущностей строят другие; замку нужны их шапки с `for` и теги в
# data/entity.json. Набор берётся из настоящего каталога: кто объявил себя
# для замка, тот и сущность.
LOCK_SKILLS = tuple(manifest.slug for manifest in registry.adapters(MODULES_DIR / SLUG, SLUG))
ENTITY_MANIFEST = 'slug = "{slug}"\nfor = ["craft-type-lock"]\n'
# Шаблон тега: запись в сущность проходит, когда в этом ходе прочитан любой
# из шаблонов её тега. Ид берутся из настоящих data/entity.json.
ENTITY_DATA = {slug: json.loads((MODULES_DIR / slug / 'data' / 'entity.json').read_text(encoding='utf-8'))
               for slug in LOCK_SKILLS}
TEMPLATE = {slug: next(iter(data['templates'].values()))[0] for slug, data in ENTITY_DATA.items()}
TASK_TEMPLATES = ENTITY_DATA['craft-task']['templates']['#задача']
# Команду Craft замок разбирает библиотекой соседа.
PARSER = 'command-tree-craft'
CRAFT_WRITE = 'mcp__Craft__craft_write'
TASK_ADD = ('blocks add --siblingId X --position after --json '
            '\'{"type":"text","markdown":"- [ ] Купить молоко #задача/5м"}\'')


def prompt(session: str) -> dict:
    return {'hook_event_name': 'UserPromptSubmit', 'session_id': session,
            'cwd': '/work', 'prompt': 'дальше'}


def write(session: str, command: str, tool: str = CRAFT_WRITE) -> dict:
    return {'hook_event_name': 'PreToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': tool, 'tool_input': {'command': command}}


def written(raw: dict) -> dict:
    """Та же запись после того, как инструмент её исполнил: `post-tool` с ответом Craft."""
    return {**raw, 'hook_event_name': 'PostToolUse',
            'tool_response': [{'type': 'text', 'text': '{"success":true}'}]}


def skill(session: str, name: str) -> dict:
    return {'hook_event_name': 'PostToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': 'Skill', 'tool_input': {'skill': name},
            'tool_response': {'success': True}}


def compact(session: str) -> dict:
    return {'hook_event_name': 'SessionStart', 'session_id': session,
            'cwd': '/work', 'source': 'compact'}


EVENT_OF = {'UserPromptSubmit': 'prompt', 'PreToolUse': 'pre-tool',
            'PostToolUse': 'post-tool', 'SessionStart': 'after-compact'}


class LockHarness(unittest.TestCase):
    api_base = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        modules = cls.root / 'modules'
        ignore = shutil.ignore_patterns('__pycache__')
        module_dir = modules / SLUG
        shutil.copytree(MODULES_DIR / SLUG, module_dir, ignore=ignore)
        shutil.copytree(CORE_SOURCE, module_dir / '_core', ignore=ignore)
        shutil.copytree(MODULES_DIR / PARSER, modules / PARSER, ignore=ignore)
        # Соседи-сущности: шапка и теги в данных, как у настоящих модулей.
        for slug in LOCK_SKILLS:
            (modules / slug).mkdir()
            (modules / slug / 'module.toml').write_text(ENTITY_MANIFEST.format(slug=slug),
                                                        encoding='utf-8')
            (modules / slug / 'data').mkdir()
            shutil.copy(MODULES_DIR / slug / 'data' / 'entity.json',
                        modules / slug / 'data' / 'entity.json')
        cls.entry = module_dir / 'hooks' / 'module.py'

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def run_raw(self, raw: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.entry), '--harness', 'claude',
             '--event', EVENT_OF[raw['hook_event_name']]],
            input=json.dumps(raw, ensure_ascii=False), text=True, capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root),
                 **({'CRAFT_API_BASE': self.api_base} if self.api_base else {})},
        )

    def fire(self, raw: dict) -> subprocess.CompletedProcess:
        result = self.run_raw(raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def assert_denied(self, result, *needles: str) -> None:
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(output['hookEventName'], 'PreToolUse')
        self.assertEqual(output['permissionDecision'], 'deny')
        for needle in needles:
            self.assertIn(needle, output['permissionDecisionReason'])

    def assert_silent(self, result) -> None:
        self.assertEqual(result.stdout, '')

    def prepare(self, session: str, slug: str, name: str | None = None) -> None:
        """Скилл сущности вызван и её шаблон прочитан в этом ходе."""
        self.assert_silent(self.fire(skill(session, name or slug)))
        self.assert_silent(self.fire(read(session, f'blocks get {TEMPLATE[slug]} --depth -1')))

    def storage(self, session: str) -> Storage:
        return Storage(self.root / '.local' / 'state' / 'jarvis', session)

    def trace(self, session: str) -> list[dict]:
        return Trace(self.storage(session)).read()

    def last_reason(self, session: str) -> str:
        return self.trace(session)[-1]['reason']


def load_lock_module():
    """Модуль замка как библиотека: ядро уже на пути у пакета тестов."""
    spec = importlib.util.spec_from_file_location(
        'craft_type_lock_hook', MODULES_DIR / SLUG / 'hooks' / 'module.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entity_body(tag: str) -> str:
    return json.dumps({'tags': [tag], 'templates': {tag: [TASK_TEMPLATES[0]]}}, ensure_ascii=False)


# --- тип страницы цели: место записи через connect-API ----------------------

PAGE = 'aaaaaaaa-0000-0000-0000-000000000001'
TOGGLE = 'aaaaaaaa-0000-0000-0000-000000000010'
D1 = 'aaaaaaaa-0000-0000-0000-000000000011'
D2 = 'aaaaaaaa-0000-0000-0000-000000000012'
LINE = 'aaaaaaaa-0000-0000-0000-000000000020'
NOTES = 'aaaaaaaa-0000-0000-0000-000000000021'
FRESH = 'aaaaaaaa-0000-0000-0000-000000000030'
FRESH_PAGE = 'aaaaaaaa-0000-0000-0000-000000000040'
PROJECT = 'bbbbbbbb-0000-0000-0000-000000000001'
STAGE = 'bbbbbbbb-0000-0000-0000-000000000010'
STAGE_TOGGLE = 'bbbbbbbb-0000-0000-0000-000000000011'
SD1 = 'bbbbbbbb-0000-0000-0000-000000000012'
REQ = 'aaaaaaaa-0000-0000-0000-000000000005'
DEEP_PAGE = 'cccccccc-0000-0000-0000-000000000001'
DEEP_D1 = 'cccccccc-0000-0000-0000-000000000011'
TASK = 'bbbbbbbb-0000-0000-0000-000000000020'
TASK_BODY = 'bbbbbbbb-0000-0000-0000-000000000021'
BARE_TASK = 'bbbbbbbb-0000-0000-0000-000000000025'
STAGE_LINK = 'bbbbbbbb-0000-0000-0000-000000000030'
STAGE2 = 'bbbbbbbb-0000-0000-0000-000000000040'
TASK2 = 'bbbbbbbb-0000-0000-0000-000000000041'
MILESTONE = 'bbbbbbbb-0000-0000-0000-000000000050'
M_STAGE = 'bbbbbbbb-0000-0000-0000-000000000051'
M_TASK = 'bbbbbbbb-0000-0000-0000-000000000052'
SPHERE = 'eeeeeeee-1000-0000-0000-000000000001'
ARCHIVE = 'eeeeeeee-1000-0000-0000-000000000010'
ARCHIVE_LINE = 'eeeeeeee-1000-0000-0000-000000000011'
REPAIR = 'eeeeeeee-1000-0000-0000-000000000020'
REPAIR_STAGE = 'eeeeeeee-1000-0000-0000-000000000022'
NOTE = 'eeeeeeee-1000-0000-0000-000000000030'
MENU = 'eeeeeeee-1000-0000-0000-000000000031'
MENU_LINE = 'eeeeeeee-1000-0000-0000-000000000032'
DAY = 'ffffffff-0000-0000-0000-000000000001'
DAY_LINE = 'ffffffff-0000-0000-0000-000000000002'


def block(block_id: str, markdown: str, **fields) -> dict:
    return {'id': block_id, 'type': 'text', 'markdown': markdown, **fields}


def page(block_id: str, markdown: str, *content, **fields) -> dict:
    return {'id': block_id, 'type': 'page', 'markdown': markdown, **fields, 'content': list(content)}


DEEP_TREE = page(DEEP_PAGE, 'Вложенная',
                 block('cccccccc-0000-0000-0000-000000000010', '+ ### Решения', listStyle='toggle'),
                 block(DEEP_D1, '  - Глубокое решение', indentationLevel=1, listStyle='bullet'))
# Корни документов. Поиск, как у настоящего API, ищет по документу блока.
# Страница проекта устроена, как в Craft: этапы на +1 под переключателем,
# задачи на +1 под своим этапом, этапы вехи на +1 под вехой.
DOCUMENTS = (
    page(PAGE, 'Раздел #раздел',
         block('aaaaaaaa-0000-0000-0000-000000000003', '### Требования', textStyle='h3'),
         block(REQ, '- Требование', listStyle='bullet'),
         block(TOGGLE, '+ ### Решения', textStyle='h3', listStyle='toggle'),
         block(D1, '  - Первое решение', indentationLevel=1, listStyle='bullet'),
         block(D2, '  - Второе решение', indentationLevel=1, listStyle='bullet'),
         block(FRESH, '  - Только что записанное решение', indentationLevel=1, listStyle='bullet'),
         page(FRESH_PAGE, 'Свежая подстраница', block('aaaaaaaa-0000-0000-0000-000000000041', 'Текст')),
         {'id': LINE, 'type': 'line', 'lineStyle': 'pageBreak', 'markdown': '*******'},
         block(NOTES, '# Заметки', textStyle='h1'),
         block('aaaaaaaa-0000-0000-0000-000000000022', 'Справочное.')),
    page(PROJECT, 'Проект #проект',
         block('bbbbbbbb-0000-0000-0000-000000000002', '+ ### Этапы проекта', listStyle='toggle'),
         page(STAGE, '  - [ ] ### Этап #этап',
              block(STAGE_TOGGLE, '+ ### Решения', textStyle='h3', listStyle='toggle'),
              block(SD1, '  - Решение этапа', indentationLevel=1, listStyle='bullet'),
              DEEP_TREE, indentationLevel=1, listStyle='task'),
         page(TASK, '    - [ ] Задача #задача/1ч', block(TASK_BODY, '### Описание', textStyle='h3'),
              indentationLevel=2, listStyle='task'),
         block(BARE_TASK, '    - [ ] Задача без тела #задача', indentationLevel=2, listStyle='task'),
         block(STAGE2, '  - [ ] ### Второй этап #этап', indentationLevel=1, listStyle='task'),
         block(TASK2, '    - [ ] Задача второго этапа #задача', indentationLevel=2, listStyle='task'),
         block(MILESTONE, '  - [ ] ## Веха #веха', indentationLevel=1, listStyle='task'),
         block(M_STAGE, '    - [ ] ### Этап вехи #этап', indentationLevel=2, listStyle='task'),
         block(M_TASK, '      - [ ] Задача этапа вехи #задача', indentationLevel=3, listStyle='task'),
         block(STAGE_LINK, f'[Этап #этап](block://{STAGE})')),
    # Сфера: подстраница «Архив» без тега и заметка с подстраницей без тега.
    page(SPHERE, 'Дом #сфера',
         page(ARCHIVE, 'Архив', block(ARCHIVE_LINE, 'Строка архива')),
         block('eeeeeeee-1000-0000-0000-000000000002', '# Активное', textStyle='h1'),
         page(REPAIR, 'Ремонт #проект',
              block('eeeeeeee-1000-0000-0000-000000000021', '+ ### Этапы проекта', listStyle='toggle'),
              block(REPAIR_STAGE, '  - [ ] ### Этап ремонта #этап', indentationLevel=1,
                    listStyle='task')),
         page(NOTE, 'Банкет #заметка', page(MENU, 'Меню', block(MENU_LINE, 'Салат с крабом')))),
    # Документ без тега: заметка дня.
    page(DAY, 'Сегодня', block(DAY_LINE, 'Купить хлеб')),
)
UPPER_PAGE = 'DDDDDDDD-0000-0000-0000-000000000001'
UPPER_D1 = 'DDDDDDDD-0000-0000-0000-000000000011'
DOCUMENTS += (page(UPPER_PAGE, 'Старый раздел #раздел',
                   block('DDDDDDDD-0000-0000-0000-000000000010', '+ ### Решения', listStyle='toggle'),
                   block(UPPER_D1, '  - Решение', indentationLevel=1, listStyle='bullet')),)
# Проект с «Решениями проекта» под переключателем: решения — его.
TAGGED_PROJECT = 'eeeeeeee-0000-0000-0000-000000000001'
PROJECT_D1 = 'eeeeeeee-0000-0000-0000-000000000011'
DOCUMENTS += (page(TAGGED_PROJECT, 'Свадьба #проект',
                   block('eeeeeeee-0000-0000-0000-000000000010', '+ # Решения проекта', listStyle='toggle'),
                   block(PROJECT_D1, '  - Решение проекта', indentationLevel=1, listStyle='bullet')),)
# Документ, где API отдал отступ не числом.
BAD_PAGE = 'abababab-0000-0000-0000-000000000001'
BAD_BLOCK = 'abababab-0000-0000-0000-000000000002'
BAD_NEXT = 'abababab-0000-0000-0000-000000000003'
DOCUMENTS += (page(BAD_PAGE, 'Сломанная',
                   block(BAD_BLOCK, 'Строка с испорченным отступом', indentationLevel='два'),
                   block(BAD_NEXT, 'Соседняя строка')),)
# Блоки, которых поиск ещё не видит: только что записанное.
NOT_INDEXED = {FRESH, FRESH_PAGE}
LINK_TEXT = re.compile(r'\[([^\]]*)\]\([^)]*\)')
MARKUP_PREFIX = re.compile(r'^\s*(?:[-+]\s+)?(?:\[[ x]\]\s+)?(?:#+\s+)?')


def plain(markdown: str) -> str:
    """Текст блока, как его видит поиск API: без адресов ссылок и разметки строки."""
    return MARKUP_PREFIX.sub('', LINK_TEXT.sub(r'\1', markdown or ''))


def index(tree: dict, document: str, pages: list, found: dict) -> dict:
    """id → (блок, документ, страницы от корня до той, где он лежит)."""
    found[tree['id']] = (tree, document, pages)
    inner = [*pages, tree] if tree.get('type') == 'page' else pages
    for child in tree.get('content', []):
        index(child, document, inner, found)
    return found


BLOCKS = {}
for document in DOCUMENTS:
    index(document, document['id'], [], BLOCKS)


def prune(tree, depth: int):
    """Дерево не глубже depth, как отдаёт connect-API по maxDepth; -1 — целиком."""
    if not isinstance(tree, dict) or depth < 0:
        return tree
    if depth == 0:
        return {key: value for key, value in tree.items() if key != 'content'}
    children = tree.get('content')
    if not isinstance(children, list):
        return tree
    return {**tree, 'content': [prune(child, depth - 1) for child in children]}


def search_answer(block_id: str, pattern: str) -> dict:
    document = BLOCKS[block_id][1]
    items = []
    for found_id, (found, owner, pages) in BLOCKS.items():
        if owner != document or found_id in NOT_INDEXED:
            continue
        if re.search(pattern, plain(found.get('markdown')), re.IGNORECASE):
            items.append({'blockId': found_id, 'markdown': plain(found.get('markdown')),
                          'pageBlockPath': [{'id': item['id'], 'content': plain(item['markdown'])}
                                            for item in pages]})
    return {'items': items}


class FakeCraftApi(http.server.BaseHTTPRequestHandler):
    # Запросы замка по порядку: путь и параметры. Цена проверки места видна по ним.
    requests: list = []

    def answer(self, code: int, body) -> None:
        data = json.dumps(body).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        endpoint = parsed.path.rsplit('/api/v1/', 1)[-1]
        type(self).requests.append((endpoint, query))
        # Как настоящий connect-API: регистр id различается.
        key = query.get('blockId' if endpoint == 'blocks/search' else 'id', '')
        if key not in BLOCKS:
            self.answer(404, {'error': 'Block not found'})
        elif endpoint == 'blocks/search':
            self.answer(200, search_answer(key, query['pattern']))
        else:
            self.answer(200, prune(BLOCKS[key][0], int(query.get('maxDepth', '-1'))))

    def log_message(self, *args) -> None:
        pass


def read(session: str, command: str) -> dict:
    return {'hook_event_name': 'PostToolUse', 'session_id': session, 'cwd': '/work',
            'tool_name': 'mcp__Craft__craft_read', 'tool_input': {'command': command},
            'tool_response': {'ok': True}}


BULLET_JSON = '\'{"type":"text","indentationLevel":1,"listStyle":"bullet","markdown":"Новое решение"}\''
SECTION = 'Запись задевает сущность «Раздел #раздел»'
# Новая задача на +1 под этапом и строка без отступа.
TASK_JSON = '\'{"type":"text","indentationLevel":2,"listStyle":"task","markdown":"- [ ] Новая задача #задача"}\''
LEVEL0_JSON = '\'{"type":"text","markdown":"Строка"}\''
FIRST = 'aaaaaaaa-0000-0000-0000-000000000003'
DOWN = 'http://127.0.0.1:9/api/v1'


class ApiLockHarness(LockHarness):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakeCraftApi)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.api_base = f'http://127.0.0.1:{cls.server.server_address[1]}/api/v1'

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        super().tearDownClass()

    def start(self, session: str, *pages: str) -> None:
        self.fire(prompt(session))
        for page_id in pages:
            self.assert_silent(self.fire(read(session, f'blocks get {page_id} --depth -1')))

    def reason(self, result) -> str:
        return json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason']

    def with_api_down(self, raw: dict):
        self.api_base = DOWN
        try:
            return self.fire(raw)
        finally:
            self.api_base = type(self).api_base
