"""Текст страниц PDF с координатами. Только то, что нужно банковским выпискам: Flate,
потоки объектов, шрифты с ToUnicode и операторы Tf, Tm, Td, Tj, TJ."""

import re
import zlib

OBJECT = re.compile(rb'(\d+) 0 obj(.*?)endobj', re.S)
ESCAPES = {b'n': b'\n', b'r': b'\r', b't': b'\t', b'b': b'\b', b'f': b'\f'}
TOKEN = re.compile(rb'/(\w+)\s+[\d.]+\s+Tf'
                   rb'|([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm'
                   rb'|([-\d.]+)\s+([-\d.]+)\s+Td'
                   rb'|(\((?:\\.|[^\\)])*\)|<[0-9a-fA-F\s]*>)\s*Tj'
                   rb'|\[((?:\((?:\\.|[^\\)])*\)|<[0-9a-fA-F\s]*>|[-\d.\s])*)\]\s*TJ', re.S)
STRING = re.compile(rb'\((?:\\.|[^\\)])*\)|<[0-9a-fA-F\s]*>', re.S)


def decode_stream(body: bytes) -> bytes:
    start = body.index(b'stream') + len(b'stream')
    start += 2 if body[start:start + 2] == b'\r\n' else 1
    data = body[start:body.rindex(b'endstream')].rstrip(b'\r\n')
    return zlib.decompress(data) if b'/FlateDecode' in body[:start] else data


def read_objects(data: bytes) -> dict:
    objects = {int(m.group(1)): m.group(2) for m in OBJECT.finditer(data)}
    for body in list(objects.values()):
        if not re.search(rb'/Type\s*/ObjStm', body[:300]):
            continue
        packed = decode_stream(body)
        first = int(re.search(rb'/First (\d+)', body).group(1))
        numbers = list(map(int, packed[:first].split()))
        pairs = list(zip(numbers[0::2], numbers[1::2]))
        for index, (number, offset) in enumerate(pairs):
            end = pairs[index + 1][1] if index + 1 < len(pairs) else len(packed) - first
            objects.setdefault(number, packed[first + offset:first + end])
    return objects


def reference(body: bytes, key: bytes) -> int | None:
    match = re.search(rb'/' + key + rb'\s+(\d+) 0 R', body)
    return int(match.group(1)) if match else None


def read_cmap(text: str) -> tuple[dict, list]:
    """Отдельные коды и диапазоны: диапазон не разворачивается в таблицу на 65 536 кодов."""
    chars, ranges = {}, []
    for block in re.findall(r'beginbfrange(.*?)endbfrange', text, re.S):
        for low, high, target in re.findall(r'<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>', block):
            ranges.append((int(low, 16), int(high, 16), int(target, 16)))
    for block in re.findall(r'beginbfchar(.*?)endbfchar', text, re.S):
        for code, target in re.findall(r'<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>', block):
            chars[int(code, 16)] = bytes.fromhex(target).decode('utf-16-be')
    return chars, ranges


def unicode_for(cmap: tuple, code: int) -> str:
    chars, ranges = cmap
    if code in chars:
        return chars[code]
    for low, high, target in ranges:
        if low <= code <= high:
            return chr(target + code - low)
    return '�'


def string_bytes(token: bytes) -> bytes:
    if token.startswith(b'<'):
        return bytes.fromhex(re.sub(rb'\s', b'', token[1:-1]).decode())
    return re.sub(rb'\\([0-7]{1,3}|.)', lambda m: bytes([int(m.group(1), 8) & 255])
                  if m.group(1)[:1].isdigit() else ESCAPES.get(m.group(1), m.group(1)), token[1:-1], flags=re.S)


def decode_text(raw: bytes, cmap: tuple | None) -> str:
    if cmap is None:
        return raw.decode('latin1')
    return ''.join(unicode_for(cmap, int.from_bytes(raw[i:i + 2], 'big')) for i in range(0, len(raw) - 1, 2))


def page_items(content: bytes, fonts: dict) -> list:
    font, x, y, items = None, 0.0, 0.0, []
    for match in TOKEN.finditer(content):
        if match.group(1):
            font = match.group(1).decode()
        elif match.group(2):
            x, y = float(match.group(6)), float(match.group(7))
        elif match.group(8):
            x, y = x + float(match.group(8)), y + float(match.group(9))
        else:
            strings = [match.group(10)] if match.group(10) else STRING.findall(match.group(11))
            text = ''.join(decode_text(string_bytes(s), fonts.get(font)) for s in strings)
            items.append((x, y, text))
    return items


def read_pages(data: bytes) -> list:
    if not data.startswith(b'%PDF-'):
        raise ValueError('Ожидается PDF')
    objects = read_objects(data)
    pages = []
    for number in sorted(objects):
        body = objects[number]
        if not re.search(rb'/Type\s*/Page(?!s)', body) or reference(body, b'Contents') is None:
            continue
        fonts = {}
        for name, font in re.findall(rb'/(F\w*)\s+(\d+) 0 R', body):
            unicode_ref = reference(objects.get(int(font), b''), b'ToUnicode')
            fonts[name.decode()] = (read_cmap(decode_stream(objects[unicode_ref]).decode('latin1'))
                                    if unicode_ref in objects else None)
        pages.append(page_items(decode_stream(objects[reference(body, b'Contents')]), fonts))
    if not pages:
        raise ValueError('В PDF нет страниц с текстом')
    return pages
