"""Операции из PDF-выписки Сбера по карте."""

import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .common import amount_decimal
from .pdf_text import read_pages

MOSCOW = ZoneInfo('Europe/Moscow')
TITLE = 'Выписка по счёту'
FIRST = re.compile(r'(\d{2}\.\d{2}\.\d{4}) (\d{2}:\d{2}) (.+?) (\+?[\d ]+,\d{2})(?: ([\d ]+,\d{2}))?')
SECOND = re.compile(r'(\d{2}\.\d{2}\.\d{4}) (\d{6}) (.*)')
CARD = re.compile(r'••\s*(\d{4})')
LINE_TOLERANCE = 1.0
DESCRIPTION_X = 150


def page_lines(items: list) -> list:
    """Строки сверху вниз: элементы с близкой высотой собираются слева направо."""
    lines = []
    for x, y, text in sorted(items, key=lambda item: (-item[1], item[0])):
        if lines and abs(lines[-1]['y'] - y) <= LINE_TOLERANCE:
            lines[-1]['parts'].append((x, text))
        else:
            lines.append({'y': y, 'parts': [(x, text)]})
    return [{'x': min(x for x, _ in line['parts']),
             'text': ' '.join(' '.join(t for _, t in sorted(line['parts'])).split())} for line in lines]


def money(value: str) -> Decimal:
    return amount_decimal(value.replace('+', ''))


def parse_operations(lines: list) -> list:
    operations, current = [], None
    for line in lines:
        first, second = FIRST.fullmatch(line['text']), SECOND.fullmatch(line['text'])
        if first:
            day, time, category, amount, balance = first.groups()
            value = money(amount) * (1 if amount.startswith('+') else -1)
            stamp = datetime.strptime(f'{day} {time}', '%d.%m.%Y %H:%M').replace(tzinfo=MOSCOW)
            current = {'timestamp': stamp.isoformat(), 'amount': f'{value:.2f}', 'category': category,
                       'balance': f'{money(balance):.2f}' if balance else None,
                       'authorization_code': None, 'description': ''}
            operations.append(current)
        elif current and second and current['authorization_code'] is None:
            current['authorization_code'], current['description'] = second.group(2), second.group(3)
        elif current and current['authorization_code'] and line['x'] >= DESCRIPTION_X:
            current['description'] = f"{current['description']} {line['text']}".strip()
        else:
            current = None
    return operations


def read_statement_pdf(data: bytes) -> dict:
    pages = [page_lines(items) for items in read_pages(data)]
    text = [line['text'] for page in pages for line in page]
    if not any(line.startswith(TITLE) for line in text):
        raise ValueError('Это не выписка Сбера по счёту')
    cards = {match.group(1) for line in text if line.startswith('Карта') for match in [CARD.search(line)] if match}
    if len(cards) != 1:
        raise ValueError('В выписке не найдена одна карта')
    operations = [row for page in pages for row in parse_operations(page)]
    return {'source': 'sber-statement', 'card_suffix': cards.pop(), 'operations': operations}
