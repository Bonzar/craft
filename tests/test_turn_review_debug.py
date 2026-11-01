"""Отладочный вывод turn-review: итог ревью дописывается к показу ответа модели."""

import json
import unittest
from datetime import datetime, timezone

from .test_turn_review import TurnReviewCase, prompt, remark, stop, write

SEPARATOR = '\n\n---\n'


def message(session: str, delta: str, final: bool = True) -> dict:
    return {'hook_event_name': 'MessageDisplay', 'session_id': session, 'cwd': '/work',
            'turn_id': 't-1', 'message_id': 'm-1', 'index': 0, 'final': final,
            'delta': delta}


class TurnReviewDebugTest(TurnReviewCase):

    def setUp(self) -> None:
        self.debug_file.write_text(json.dumps({'debug': True}), encoding='utf-8')

    def tearDown(self) -> None:
        self.debug_file.unlink(missing_ok=True)

    def shown(self, result) -> str:
        payload = json.loads(result.stdout)
        self.assertEqual(payload['hookSpecificOutput']['hookEventName'], 'MessageDisplay')
        return payload['hookSpecificOutput']['displayContent']

    def clean_review(self, session: str) -> None:
        """Ход с записью: одно поручение, затем чистый вердикт принят на stop."""
        self.start_with_write(session)
        self.blocked(self.fire(stop(session)))
        self.write_verdict(session, self.state(session)['volume_id'])
        self.assert_silent(self.fire(stop(session)))

    def test_debug_off_keeps_model_message_silent(self) -> None:
        self.debug_file.unlink()
        self.clean_review('d-off')
        self.assert_silent(self.fire(message('d-off', 'Готово.')))
        self.assertIsNone(self.state('d-off')['summary'])
        self.assertIsNone(self.state('d-off')['first_nudge_at'])

    def test_debug_false_keeps_model_message_silent(self) -> None:
        self.debug_file.write_text(json.dumps({'debug': False}), encoding='utf-8')
        self.clean_review('d-false')
        self.assert_silent(self.fire(message('d-false', 'Готово.')))

    def test_first_nudge_time_is_written_in_utc(self) -> None:
        self.start_with_write('d-time')
        self.blocked(self.fire(stop('d-time')))
        started = datetime.fromisoformat(self.state('d-time')['first_nudge_at'])
        self.assertEqual(started.utcoffset(), timezone.utc.utcoffset(None))

    def test_a_clean_verdict_is_shown_once_with_the_delta(self) -> None:
        self.clean_review('d-clean')
        text = self.shown(self.fire(message('d-clean', 'Готово.')))
        delta, report = text.split(SEPARATOR)
        self.assertEqual(delta, 'Готово.')
        lines = report.split('\n')
        self.assertEqual(lines[0], 'Ревью хода: чисто.')
        self.assertEqual(lines[1], 'Критерии: craft, consistency, anchor. Записей write 1, read 0.')
        self.assertRegex(lines[2], r'^Поручений 1, с первого прошло \d+ с\.$')
        self.assertTrue(self.state('d-clean')['summary_shown'])
        self.assert_silent(self.fire(message('d-clean', 'Ещё.')))

    def test_remarks_are_listed_in_the_report(self) -> None:
        self.start_with_write('d-remark')
        self.write_verdict('d-remark', self.state('d-remark')['volume_id'], [remark()])
        self.blocked(self.fire(stop('d-remark')))
        report = self.shown(self.fire(message('d-remark', 'Чиню.'))).split(SEPARATOR)[1]
        self.assertIn('Ревью хода: замечания (1): [craft] нет ссылки на этап.', report)
        self.assertIn('Поручений не было.', report)

    def test_a_release_by_the_human_is_reported(self) -> None:
        self.start_with_write('d-release')
        self.nudge_three_times('d-release')
        self.fire(stop('d-release'))
        self.fire(prompt('d-release', 'закончить без ревью'))
        self.assert_silent(self.fire(stop('d-release')))
        report = self.shown(self.fire(message('d-release', 'Ок.'))).split(SEPARATOR)[1]
        self.assertIn('Ревью хода: без ревью, по решению человека.', report)
        self.assertIn('Записей write 1, read 0.', report)
        self.assertIn('Поручений 3, с первого прошло', report)

    def test_a_verdict_in_the_file_is_shown_under_the_answer_of_its_turn(self) -> None:
        session = 'd-preview'
        self.start_with_write(session)
        self.blocked(self.fire(stop(session)))
        self.write_verdict(session, self.state(session)['volume_id'])
        text = self.shown(self.fire(message(session, 'Ревью прошло.')))
        delta, report = text.split(SEPARATOR)
        self.assertEqual(delta, 'Ревью прошло.')
        self.assertIn('Ревью хода: чисто.', report)
        self.assertIn('Критерии: craft, consistency, anchor. Записей write 1, read 0.', report)
        self.assertIn('Поручений 1, с первого прошло', report)
        self.assert_silent(self.fire(message(session, 'Ещё.')))
        self.assert_silent(self.fire(stop(session)))
        self.assertEqual(self.last_reason(session), 'ревью чистое, ход отпущен')
        self.assertTrue(self.state(session)['summary_shown'])
        self.assert_silent(self.fire(message(session, 'Следующий ход.')))

    def test_previewed_remarks_are_not_shown_again_after_the_stop(self) -> None:
        session = 'd-preview-remark'
        self.start_with_write(session)
        self.write_verdict(session, self.state(session)['volume_id'], [remark()])
        report = self.shown(self.fire(message(session, 'Готово.'))).split(SEPARATOR)[1]
        self.assertIn('замечания (1): [craft] нет ссылки на этап.', report)
        self.assertIn('нет ссылки на этап', self.blocked(self.fire(stop(session))))
        self.assert_silent(self.fire(message(session, 'Чиню.')))

    def test_a_verdict_of_another_volume_is_not_previewed(self) -> None:
        self.start_with_write('d-foreign')
        self.write_verdict('d-foreign', 'старый-объём')
        self.assert_silent(self.fire(message('d-foreign', 'Готово.')))

    def test_a_final_message_without_a_summary_is_silent(self) -> None:
        self.fire(prompt('d-empty'))
        self.assert_silent(self.fire(message('d-empty', 'Привет.')))

    def test_a_chunk_that_is_not_final_is_silent_and_keeps_the_summary(self) -> None:
        self.clean_review('d-chunk')
        self.assert_silent(self.fire(message('d-chunk', 'Гото', final=False)))
        self.assertFalse(self.state('d-chunk')['summary_shown'])
        self.assertIn('Ревью хода:', self.shown(self.fire(message('d-chunk', 'во.'))))

    def test_a_turn_without_writes_leaves_no_summary(self) -> None:
        self.fire(prompt('d-nowrite'))
        self.assert_silent(self.fire(stop('d-nowrite')))
        self.assert_silent(self.fire(message('d-nowrite', 'Готово.')))
        self.fire(write('d-nowrite'))
        self.assert_silent(self.fire(message('d-nowrite', 'Записал.')))


if __name__ == '__main__':
    unittest.main()
