"""Дерево из прочитанного: страницы, которые агент уже читал, без похода в API.

Часть замка craft-type-lock. Кэш сессии в файле рядом с состоянием замка:

- pages — страница → её блок (тип, текст, отступ), прямые дети по порядку
  (None — не известны) и полон ли список детей;
- links — блок → страница, в которой он лежит: из детей JSON-чтения, из
  вложенности страниц markdown-чтения и из путей поиска API;
- roots — страницы, которые поиск API назвал корнем документа.

Свежее чтение страницы заменяет её поддерево. Запись в Craft сбрасывает
страницы, которых коснулась: следующая проверка места читает их заново.
Кэш живёт между ходами (дерево — факт Craft, а не хода), но не дольше
TTL_SEC: страницу мог поменять человек в приложении. Сжатие кэш стирает.

Правки кэша копятся операциями и под локом состояния накатываются на свежую
копию файла: хуки параллельных вызовов не затирают друг друга.
"""

import json

from reads import CONTENT_KEY, ID_KEY, cursor_offsets, json_reads, markdown_pages, response_text, slim

TREE_FILE = 'craft-type-lock-tree.json'
PAGES_KEY = 'pages'
LINKS_KEY = 'links'
ROOTS_KEY = 'roots'
AT_KEY = 'at'
BLOCK_KEY = 'block'
CHILDREN_KEY = 'children'
COMPLETE_KEY = 'complete'
PARENT_KEY = 'parent'
# Десять минут: столько прочитанное агентом считается правдой о Craft.
# Человек правит базу в приложении параллельно, а агент перед записью по
# правилам читает страницу заново — старое дерево к записи уже не нужно.
TTL_SEC = 600
# Потолок страниц и связей: файл читается на каждой записи в Craft.
MAX_PAGES = 30
MAX_LINKS = 3000
# Прямых детей в одном ответе чтения не больше 50 (`blocks get --help`):
# ответ ровно на 50 мог оборваться, такой список полным не считается.
PAGE_SIZE = 50


def empty() -> dict:
    return {PAGES_KEY: {}, LINKS_KEY: {}, ROOTS_KEY: {}}


def is_entry(entry) -> bool:
    children = entry.get(CHILDREN_KEY) if isinstance(entry, dict) else None
    return (isinstance(entry, dict) and isinstance(entry.get(ID_KEY), str)
            and isinstance(entry.get(AT_KEY), (int, float))
            and (children is None or isinstance(children, list)))


def check(data, path) -> dict:
    """Файл кэша как есть или ValueError с путём: порча — не тихий сброс."""
    ok = isinstance(data, dict) and all(isinstance(data.get(key), dict)
                                        for key in (PAGES_KEY, LINKS_KEY, ROOTS_KEY))
    if not ok or not all(is_entry(entry) for entry in data[PAGES_KEY].values()):
        raise ValueError(f'файл дерева замка {path} испорчен. Удали его, дерево соберётся заново')
    return data


class Tree:
    """Кэш дерева одной сессии и операции над ним, записанные для повтора."""

    def __init__(self, data: dict, now: float) -> None:
        self.data = data
        self.now = now
        self.ops: list[tuple[str, tuple]] = []

    # --- чтение -----------------------------------------------------------

    def fresh(self, record) -> bool:
        return isinstance(record, dict) and self.now - record.get(AT_KEY, 0) <= TTL_SEC

    def page(self, page_id: str) -> dict | None:
        entry = self.data[PAGES_KEY].get(page_id.lower())
        return entry if self.fresh(entry) else None

    def parent(self, block_id: str) -> str | None:
        link = self.data[LINKS_KEY].get(block_id.lower())
        return link[PARENT_KEY] if self.fresh(link) else None

    def is_root(self, page_id: str) -> bool:
        at = self.data[ROOTS_KEY].get(page_id.lower())
        return at is not None and self.now - at <= TTL_SEC

    def block(self, block_id: str) -> dict | None:
        """Блок из прочитанного: свой блок страницы или ребёнок страницы над ним."""
        entry = self.page(block_id)
        if entry is not None and entry.get(BLOCK_KEY):
            return entry[BLOCK_KEY]
        parent = self.parent(block_id)
        above = self.page(parent) if parent else None
        for child in (above or {}).get(CHILDREN_KEY) or []:
            if child[ID_KEY].lower() == block_id.lower():
                return child
        return None

    def is_unknown(self, page_id: str) -> bool:
        """Страница в кэше, а её дети — нет: прочитана markdown или сброшена записью."""
        entry = self.page(page_id)
        return entry is not None and entry.get(CHILDREN_KEY) is None

    def children(self, page_id: str) -> tuple[list[dict] | None, bool]:
        """Дети страницы и полнота списка; не известны — (None, False)."""
        entry = self.page(page_id)
        if entry is None or entry.get(CHILDREN_KEY) is None:
            return None, False
        return entry[CHILDREN_KEY], bool(entry.get(COMPLETE_KEY))

    def chain(self, block_id: str) -> tuple[list[str], bool]:
        """Страницы над блоком от верхней известной; второе — верх это корень документа."""
        pages, seen = [], {block_id.lower()}
        parent = self.parent(block_id)
        while parent is not None and parent.lower() not in seen:
            seen.add(parent.lower())
            pages.insert(0, parent)
            parent = self.parent(parent)
        top = pages[0] if pages else block_id
        return pages, self.is_root(top)

    # --- правка: каждая операция пишется для повтора на свежей копии -------

    def apply(self, name: str, *args) -> None:
        self.ops.append((name, args))
        getattr(self, name)(*args)

    def replay(self, ops) -> None:
        for name, args in ops:
            getattr(self, name)(*args)

    def link(self, block_id: str, parent_id: str) -> None:
        self.data[LINKS_KEY][block_id.lower()] = {PARENT_KEY: parent_id, AT_KEY: self.now}

    def unlink_children_of(self, page_id: str) -> None:
        key = page_id.lower()
        links = self.data[LINKS_KEY]
        for child in [child for child, link in links.items() if link[PARENT_KEY].lower() == key]:
            del links[child]

    def drop_subtree(self, page_id: str) -> None:
        """Страница и все страницы под ней по известным детям — вон из кэша."""
        entry = self.data[PAGES_KEY].pop(page_id.lower(), None)
        self.unlink_children_of(page_id)
        for child in (entry or {}).get(CHILDREN_KEY) or []:
            self.drop_subtree(child[ID_KEY])

    def store_page(self, node: dict, children: list[dict] | None, complete: bool) -> None:
        self.data[PAGES_KEY][node[ID_KEY].lower()] = {
            ID_KEY: node[ID_KEY], BLOCK_KEY: slim(node), CHILDREN_KEY: children,
            COMPLETE_KEY: complete, AT_KEY: self.now}
        for child in children or []:
            self.link(child[ID_KEY], node[ID_KEY])

    def learn_node(self, node: dict, complete: bool) -> None:
        """Узел JSON-чтения с детьми: заменяет поддерево страницы, вложенные — тоже."""
        content = node.get(CONTENT_KEY)
        if not isinstance(content, list):
            return
        self.drop_subtree(node[ID_KEY])
        self.store_page(node, [slim(child) for child in content], complete)
        for child in content:
            self.learn_node(child, True)

    def learn_json(self, root: dict, offset: int, next_cursor: str | None) -> None:
        """Ответ JSON-чтения. Продолжение по курсору дописывает детей, когда стыкуется."""
        content = root.get(CONTENT_KEY)
        if not isinstance(content, list):
            return
        if offset == 0:
            self.learn_node(root, next_cursor is None and len(content) < PAGE_SIZE)
            return
        known, complete = self.children(root[ID_KEY])
        if known is not None and not complete and len(known) == offset:
            self.store_page(root, [*known, *(slim(child) for child in content)],
                            next_cursor is None and len(content) < PAGE_SIZE)
        for child in content:
            self.learn_node(child, True)

    def learn_markdown(self, pages: list[tuple[str, str | None]]) -> None:
        """Markdown-чтение: страницы прочитаны заново, известна только их вложенность.

        Страница встаёт без блока и детей: она кандидат на один запрос детей.
        """
        for page_id, _ in pages:
            self.drop_subtree(page_id)
            self.unknown(page_id, keep_block=False)
        for page_id, parent in pages:
            if parent is not None:
                self.link(page_id, parent)

    def unknown(self, page_id: str, keep_block: bool) -> None:
        """Дети страницы больше не известны; блок — тоже, если не keep_block."""
        entry = self.data[PAGES_KEY].setdefault(page_id.lower(), {ID_KEY: page_id, BLOCK_KEY: None})
        entry.update({CHILDREN_KEY: None, COMPLETE_KEY: False, AT_KEY: self.now})
        if not keep_block:
            entry[BLOCK_KEY] = None
        self.unlink_children_of(page_id)

    def learn_children(self, page: dict, children: list[dict]) -> None:
        """Дети страницы из API (`maxDepth=1` или дерево прочитанной страницы)."""
        self.store_page(page, [slim(child) for child in children], True)

    def learn_path(self, block_id: str, path: list[str]) -> None:
        """Путь поиска API: страницы от корня документа до той, где лежит блок."""
        if path:
            self.data[ROOTS_KEY][path[0].lower()] = self.now
        else:
            self.data[ROOTS_KEY][block_id.lower()] = self.now
        for above, below in zip(path, [*path[1:], block_id]):
            self.link(below, above)

    def forget(self, block_ids: list[str]) -> None:
        """После записи: дети задетых страниц и страниц над целями не известны."""
        for block_id in block_ids:
            parent = self.parent(block_id)
            if block_id.lower() in self.data[PAGES_KEY]:
                self.unknown(block_id, keep_block=False)
            self.data[LINKS_KEY].pop(block_id.lower(), None)
            if parent is not None:
                self.unknown(parent, keep_block=True)

    def prune(self) -> None:
        """Старше TTL_SEC — вон; страниц не больше MAX_PAGES, связей — MAX_LINKS."""
        for key in (PAGES_KEY, LINKS_KEY):
            kept = sorted(((k, v) for k, v in self.data[key].items() if self.fresh(v)),
                          key=lambda item: item[1][AT_KEY], reverse=True)
            self.data[key] = dict(kept[:MAX_PAGES if key == PAGES_KEY else MAX_LINKS])
        self.data[ROOTS_KEY] = {k: at for k, at in self.data[ROOTS_KEY].items()
                                if self.now - at <= TTL_SEC}


def learn_read(tree: Tree, command: str, result, craft) -> int:
    """Ответ `craft_read` — в дерево: JSON целиком, из markdown — вложенность страниц.

    Возвращает число страниц, о которых дерево узнало.
    """
    text = response_text(result)
    found = json_reads(text)
    offsets = cursor_offsets(command, craft)
    for read in found:
        tree.apply('learn_json', read.root, offsets.get(read.root[ID_KEY].lower(), 0), read.next_cursor)
    # В JSON кавычки внутри текста экранированы: тег страницы там не найдётся.
    pages = [(page, parent) for page, parent in markdown_pages(text)
             if craft.is_block_id(page) and (parent is None or craft.is_block_id(parent))]
    if pages:
        tree.apply('learn_markdown', pages)
    return len(found) + len(pages)


def load(storage, now: float) -> Tree:
    path = storage.path(TREE_FILE)
    try:
        data = storage.read_json(TREE_FILE, default=None)
    except json.JSONDecodeError as error:
        raise ValueError(f'файл дерева замка {path} не JSON: {error}. Удали его, '
                         'дерево соберётся заново') from error
    return Tree(empty() if data is None else check(data, path), now)


def save(storage, tree: Tree) -> None:
    """Накатить операции дерева на свежую копию файла. Зовётся под локом состояния."""
    if not tree.ops:
        return
    fresh = load(storage, tree.now)
    fresh.replay(tree.ops)
    fresh.prune()
    storage.write_json(TREE_FILE, fresh.data)


def clear(storage) -> None:
    storage.write_json(TREE_FILE, empty())
