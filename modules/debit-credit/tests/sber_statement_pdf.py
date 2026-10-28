"""Синтетическая выписка Сбера в PDF: та же раскладка, что у настоящей, без личных данных."""

import zlib

CARD_LINE = 'Кредитная СберКарта •• 0002'
IDENTITY_CMAP = (b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n'
                 b'1 begincodespacerange <0000><FFFF> endcodespacerange\n'
                 b'1 beginbfrange\n<0000><FFFF><0000>\nendbfrange\nendcmap end end\n')


def encode(text: str) -> bytes:
    raw = text.encode('utf-16-be')
    return raw.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')


def content(lines: list) -> bytes:
    """lines: (x, y, text) в координатах страницы."""
    parts = [b'BT\n/F1 8 Tf\n']
    for x, y, text in lines:
        parts.append(f'1 0 0 1 {x} {y} Tm\n'.encode() + b'(' + encode(text) + b')Tj\n')
    parts.append(b'ET\n')
    return b''.join(parts)


def stream_object(number: int, data: bytes, compress: bool = True) -> bytes:
    body = zlib.compress(data) if compress else data
    flt = b'/Filter/FlateDecode' if compress else b''
    return (f'{number} 0 obj\n<<{flt.decode()}/Length {len(body)}>>stream\n'.encode()
            + body + b'\nendstream\nendobj\n')


def build_pdf(lines: list) -> bytes:
    objects = [
        b'1 0 obj\n<</Type/Catalog/Pages 2 0 R>>\nendobj\n',
        b'2 0 obj\n<</Type/Pages/Kids[3 0 R]/Count 1>>\nendobj\n',
        b'3 0 obj\n<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R'
        b'/MediaBox[0 0 595 842]>>\nendobj\n',
        stream_object(4, content(lines)),
        b'5 0 obj\n<</Subtype/Type0/Type/Font/BaseFont/Test/Encoding/Identity-H/ToUnicode 6 0 R>>\nendobj\n',
        stream_object(6, IDENTITY_CMAP),
    ]
    return b'%PDF-1.5\n' + b''.join(objects) + b'%%EOF\n'


def statement_lines(card_line: str = CARD_LINE) -> list:
    return [
        (48, 763.7, 'Выписка по счёту кредитной карты'),
        (48, 742.8, 'За период 01.02.2025 — 28.02.2025'),
        (60, 656.9, 'Карта'), (198, 656.9, card_line),
        (48, 485.9, 'ДАТА ОПЕРАЦИИ (МСК)'), (155, 485.9, 'КАТЕГОРИЯ'),
        (48, 440.9, '18.02.2025'), (105, 440.9, '19:34'), (155, 440.9, 'Перевод на карту'),
        (441, 440.9, '+10,00'), (523, 440.9, '136 837,42'),
        (48, 429.9, '18.02.2025'), (105, 429.9, '183191'),
        (155, 429.9, 'Перевод от Н. Операция по карте'), (155, 419.5, '****0002'),
        (48, 400.9, '14.02.2025'), (105, 400.9, '01:19'), (155, 400.9, 'Прочие операции'),
        (440, 400.9, '296,16'), (523, 400.9, '136 827,42'),
        (48, 389.9, '14.02.2025'), (105, 389.9, '831544'),
        (155, 389.9, 'SBER STRAHOVANIE ZAEMSHCHIKA Списание платы за'),
        (155, 379.5, 'страхование с карты Банка. Операция по карте ****0002'),
        (48, 360.9, '11.02.2025'), (105, 360.9, '02:41'), (155, 360.9, 'Прочие расходы'),
        (436, 360.9, '9 600,00'), (523, 360.9, '136 973,58'),
        (48, 349.9, '11.02.2025'), (105, 349.9, '299952'), (155, 349.9, 'MOSCOW YANDEX AFISHA'),
        (48, 60, 'Продолжение на следующей странице'),
    ]
