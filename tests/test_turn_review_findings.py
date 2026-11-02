"""Ревью хода turn-review: испорченный сосед на показе и потерянная запись в объёме."""

import json

from .test_turn_review import (
    CRAFT_WRITE,
    TASK_ADD,
    BrokenNeighbourTest,
    LostWritesTest,
    entity_row,
    message,
    prompt,
    review_lib,
    stop,
    write,
    write_record,
)

OTHER_ADD = 'blocks add --siblingId Y --position after --json \'{"type":"text","markdown":"Строка"}\''


class BrokenNeighbourPreviewTest(BrokenNeighbourTest):
    """Шапка соседа испорчена, а ход уже начат: показ ответа не падает и держит его спрятанным."""

    def test_a_broken_neighbour_header_after_a_prompt_keeps_the_answer_hidden(self) -> None:
        session = 's-broken-after-prompt'
        raw = prompt(session, prompt_id='p-broken-after-prompt')
        run = self.run_entry(raw, self.entry)
        self.assertEqual(run.returncode, 0, run.stderr)
        row = {'kind': 'write', 'prompt_id': raw['prompt_id'], 'tool': CRAFT_WRITE,
               'command': TASK_ADD, 'result': None}
        self.storage(session).append_line('turn-review-volume.jsonl', json.dumps(row, ensure_ascii=False))
        run = self.run_entry(message(session, 'ответ'), self.entry)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['hookSpecificOutput']['displayContent'],
                         'Ревью хода идёт, отчёт после него.')


class LostWriteAmongOthersTest(LostWritesTest):
    """Поставщик записей упал на одном вызове: строка замка осталась без пары рядом с другими записями."""

    def test_an_orphan_lock_row_next_to_other_writes_blocks_the_stop(self) -> None:
        session = 's-lost-one-write'
        raw = prompt(session)
        self.fire(raw)
        # Записанная запись с парой: поставщик положил её за тест.
        paired = write(session)
        self.fire(paired)
        self.storage(session).append_line('turn-review-volume.jsonl', json.dumps(
            {'kind': 'write', 'prompt_id': raw['prompt_id'], 'tool': CRAFT_WRITE, 'command': TASK_ADD,
             'result': None}, ensure_ascii=False))
        # Вторая запись: строка замка есть, а записи поставщик не положил.
        self.fire(write(session, OTHER_ADD))
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('без своей записи write', reason)
        self.assertIn(OTHER_ADD[:40], reason)

    def test_unclaimed_rows_are_counted_after_pairing(self) -> None:
        rows = [review_lib.parse_record(entity_row(TASK_ADD, 'p')),
                review_lib.parse_record(entity_row(OTHER_ADD, 'p'))]
        self.assertIsNone(review_lib.lost_writes_problem([rows[0], write_record(TASK_ADD, 'p')]))
        problem = review_lib.lost_writes_problem([*rows, write_record(TASK_ADD, 'p')])
        self.assertIn('без своей записи write', problem)
        self.assertIn(OTHER_ADD[:40], problem)
        self.assertNotIn('записей write в объёме нет', problem)


del BrokenNeighbourTest, LostWritesTest
