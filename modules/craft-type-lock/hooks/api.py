"""Connect-API Craft для замка craft-type-lock: блок, дети страницы, путь страниц, дерево.

Только чтение, только GET. Родителя у блока API не отдаёт (замер 02.11.2026:
ни `GET /blocks`, ни `fetchMetadata` поля родителя не несут), а путь страниц
до блока отдаёт поиск внутри документа: `GET /blocks/search` возвращает у
каждой находки `pageBlockPath` — страницы от корня документа до той, в
которой блок лежит, сам блок в путь не входит. Поэтому путь ищется по тексту
самого блока, а находка сверяется по id. Блок без слов (разделитель,
картинка) и блок, которого поиск ещё не видит, путь так не получают: их
место ищется в деревьях прочитанных страниц (`tree`).

Владельца по отступу замок ищет среди детей страницы: `GET /blocks` с
`maxDepth=1` отдаёт страницу и её прямых детей по порядку, у каждого
`indentationLevel` (поля нет — уровень 0) и текст (замер 02.11.2026: проект
на 60 детей — 46 КБ, 2 с). Поиск соседей блока (`beforeBlocks`) уровня не
несёт, поэтому он владельца не даёт.
"""

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request

BASE_ENV = 'CRAFT_API_BASE'
# Без User-Agent connect-API отвечает 403 (замер 01.11.2026 из контейнера).
USER_AGENT = 'jarvis-craft-type-lock'
API_TIMEOUT_SEC = 10
# Потолок на всю проверку места: запросов на цель до трёх, у запасного пути
# по одному на прочитанную страницу, а хук не должен держать запись минутами.
DEADLINE_SEC = 12
NOT_FOUND = 404
BLOCK_DEPTH = 0
# Страница и её прямые дети: порядок и отступы для владельца по отступу.
CHILDREN_DEPTH = 1
CONTENT_KEY = 'content'
# Запасной путь: прочитанная страница целиком. Глубина не ограничена, иначе
# цель на вложенной странице считалась бы непрочитанной.
TREE_DEPTH = -1
ID_KEY = 'id'
ITEMS_KEY = 'items'
FOUND_ID_KEY = 'blockId'
PATH_KEY = 'pageBlockPath'
# Слов текста блока в образце поиска: находку и так отличает id, а длинный
# образец чаще спотыкается о разметку, которой в индексе поиска нет.
SEARCH_WORDS = 6
# Поиск идёт по тексту без разметки (замер 02.11.2026: у задачи «- [x] Удалить
# скилл-док» находка «Удалить скилл-док»). Поэтому из markdown убираются текст
# адреса ссылок, структурные теги, маркер списка, чекбокс и решётки заголовка,
# а слова склеиваются любыми не-словесными знаками между ними.
LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')
STRUCTURAL_TAG = re.compile(r'<[^>]+>')
LINE_PREFIX = re.compile(r'^\s*(?:[-*+]\s+)?(?:\[[ xX]\]\s+)?(?:#{1,6}\s+)?')
WORD = re.compile(r'[^\W_]+')
WORD_GAP = r'\W+'
# Ошибки сети и ответа: цель с такой ошибкой остаётся без места.
FETCH_ERRORS = (urllib.error.URLError, http.client.HTTPException, OSError, ValueError)


class Late(Exception):
    """Срок проверки места вышел до следующего запроса."""


def same_id(left, right) -> bool:
    """Один ли это блок: регистр id API хранит, но здесь он не различается."""
    return isinstance(left, str) and isinstance(right, str) and left.lower() == right.lower()


def other_case(block_id: str) -> str:
    """Тот же id в другом регистре: верхний для нижнего и наоборот."""
    return block_id.lower() if block_id != block_id.lower() else block_id.upper()


def search_pattern(markdown) -> str | None:
    """Образец поиска по первым словам текста блока; слов нет — None."""
    if not isinstance(markdown, str):
        return None
    text = STRUCTURAL_TAG.sub(' ', LINK.sub(r'\1', markdown))
    words = WORD.findall(LINE_PREFIX.sub('', text, count=1))[:SEARCH_WORDS]
    return WORD_GAP.join(words) if words else None


def as_block(answer) -> dict:
    if isinstance(answer, list):
        answer = answer[0] if answer else None
    if not isinstance(answer, dict) or not isinstance(answer.get(ID_KEY), str):
        raise ValueError('ответ API не блок')  # noqa: TRY004 — порча ответа, как и в остальных ветках
    return answer


def found_path(answer, block_id: str) -> list[dict] | None:
    """Путь страниц находки с этим id; находки нет — None. Ответ не тот — ValueError."""
    items = answer.get(ITEMS_KEY) if isinstance(answer, dict) else None
    if not isinstance(items, list):
        raise ValueError('ответ поиска без списка находок')  # noqa: TRY004 — порча ответа
    for item in items:
        if not isinstance(item, dict) or not same_id(item.get(FOUND_ID_KEY), block_id):
            continue
        path = item.get(PATH_KEY)
        if not isinstance(path, list) or not all(
                isinstance(page, dict) and isinstance(page.get(ID_KEY), str) for page in path):
            raise ValueError('у находки поиска нет пути страниц')
        return path
    return None


class Api:
    """Запросы одной проверки места: общий срок и память уже полученных блоков."""

    def __init__(self, base: str, clock, tree=None) -> None:
        self.base = base.rstrip('/')
        self.clock = clock
        self.deadline = clock() + DEADLINE_SEC
        self.blocks: dict[str, dict] = {}
        self.kids: dict[str, list[dict]] = {}
        self.requests = 0
        # Дерево из прочитанного (hooks/tree.py): что в нём есть, не запрашивается,
        # а что пришло из API, ложится в него для следующих записей.
        self.read_tree = tree

    def get(self, path: str, params: dict):
        left = self.deadline - self.clock()
        if left <= 0:
            raise Late
        self.requests += 1
        request = urllib.request.Request(
            f'{self.base}/{path}?{urllib.parse.urlencode(params)}',
            headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=min(API_TIMEOUT_SEC, left)) as response:
            return json.load(response)

    def by_id(self, block_id: str, depth: int) -> dict:
        """Блок на глубину depth. 404 повторяется в другом регистре id: агент мог
        набрать id не в том регистре, в каком его знает API (замер 01.11.2026)."""
        try:
            return as_block(self.get('blocks', {'id': block_id, 'maxDepth': depth}))
        except urllib.error.HTTPError as error:
            if error.code != NOT_FOUND:
                raise
        return as_block(self.get('blocks', {'id': other_case(block_id), 'maxDepth': depth}))

    def known(self, block_id: str) -> dict | None:
        """Блок без запроса: из памяти проверки или из дерева прочитанного."""
        key = block_id.lower()
        if key not in self.blocks and self.read_tree is not None:
            cached = self.read_tree.block(block_id)
            if cached is not None:
                self.blocks[key] = cached
        return self.blocks.get(key)

    def block(self, block_id: str) -> dict:
        """Сам блок без детей: тип, стиль и текст. Повтор за проверку не запрашивается."""
        found = self.known(block_id)
        if found is None:
            found = self.blocks[block_id.lower()] = self.by_id(block_id, BLOCK_DEPTH)
        return found

    def cached_children(self, page_id: str, anchor: str | None) -> list[dict] | None:
        """Дети из дерева прочитанного, если их хватает месту: полный список или
        начало списка, в котором уже стоит anchor."""
        children, complete = self.read_tree.children(page_id) if self.read_tree is not None else (None, False)
        if children is None:
            return None
        if complete or (anchor is not None and any(same_id(child[ID_KEY], anchor) for child in children)):
            return children
        return None

    def children(self, page_id: str, anchor: str | None = None) -> list[dict]:
        """Прямые дети страницы по порядку. Сама страница и дети ложатся в память блоков."""
        key = page_id.lower()
        if key in self.kids:
            return self.kids[key]
        cached = self.cached_children(page_id, anchor)
        if cached is not None:
            self.kids[key] = cached
            return cached
        page = self.by_id(page_id, CHILDREN_DEPTH)
        content = page.get(CONTENT_KEY, [])
        if not isinstance(content, list) or not all(
                isinstance(child, dict) and isinstance(child.get(ID_KEY), str) for child in content):
            raise ValueError(f'у страницы {page_id[:8]} дети не списком блоков')
        self.store(page, content)
        return content

    def store(self, page: dict, content: list[dict]) -> None:
        """Дети страницы из API — в память проверки и в дерево прочитанного."""
        key = page[ID_KEY].lower()
        self.blocks.setdefault(key, {k: v for k, v in page.items() if k != CONTENT_KEY})
        self.kids[key] = [{k: v for k, v in child.items() if k != CONTENT_KEY} for child in content]
        for child in self.kids[key]:
            self.blocks.setdefault(child[ID_KEY].lower(), child)
        if self.read_tree is not None:
            self.read_tree.apply('learn_children', page, self.kids[key])

    def tree(self, page_id: str) -> dict:
        """Прочитанная страница со всем, что в ней лежит.

        Дети каждой страницы дерева ложатся в память: владельца по отступу
        потом не нужно запрашивать второй раз.
        """
        tree = self.by_id(page_id, TREE_DEPTH)
        self.remember(tree)
        return tree

    def remember(self, node: dict) -> None:
        content = node.get(CONTENT_KEY)
        if not isinstance(content, list) or not all(
                isinstance(child, dict) and isinstance(child.get(ID_KEY), str) for child in content):
            return
        if node[ID_KEY].lower() not in self.kids:
            self.store(node, content)
        for child in content:
            self.remember(child)

    def probe(self, page_id: str) -> None:
        """Один запрос детей страницы, где цель вероятнее всего лежит."""
        if page_id.lower() not in self.kids:
            self.children(page_id)

    def page_path(self, block: dict) -> list[dict] | None:
        """Страницы от корня документа до той, где лежит блок: `[{id, content}, …]`.

        Нечем искать (в тексте нет слов) или поиск блока не нашёл — None.
        Поиск идёт в документе блока: `blockId` — сам блок (замер 02.11.2026).
        """
        pattern = search_pattern(block.get('markdown'))
        if pattern is None:
            return None
        answer = self.get('blocks/search', {'blockId': block[ID_KEY], 'pattern': pattern,
                                            'beforeBlockCount': 0, 'afterBlockCount': 0})
        path = found_path(answer, block[ID_KEY])
        if path is not None and self.read_tree is not None:
            self.read_tree.apply('learn_path', block[ID_KEY], [page[ID_KEY] for page in path])
        return path
