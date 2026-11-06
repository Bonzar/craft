"""Настоящее ревью через обёртку Codex: вопрос и ответ текстом."""

import json
import sys

from .test_turn_review import EVENT_OF, QUESTION, TurnReviewCase, prompt, stop, tool


class CodexTurnReviewTest(TurnReviewCase):
    def command(self, raw, entry=None):
        return [sys.executable, str(entry or self.entry), '--harness', 'codex',
                '--event', EVENT_OF[raw['hook_event_name']]]

    def fire(self, raw, autonomous=False):
        # Native Codex использует turn_id, не prompt_id из Claude-фикстуры.
        native = dict(raw)
        if 'prompt_id' in native:
            native['turn_id'] = native.pop('prompt_id')
        return super().fire(native, autonomous)

    def question_after_three_nudges(self, session):
        self.start_with_write(session)
        self.nudge_three_times(session)
        result = self.fire(stop(session))
        reason = self.blocked(result)
        self.assertIn(QUESTION, reason)
        self.assertNotIn('AskUserQuestion', reason)
        return result

    def show_question(self, session):
        self.question_after_three_nudges(session)
        event = {**stop(session), 'last_assistant_message':
                 QUESTION + '\nПровести ревью\nЗакончить без ревью'}
        self.assert_silent(self.fire(event))
        self.assertTrue(self.volume(session))

    def test_shown_question_waits_without_clearing_the_volume(self):
        self.show_question('cx-wait')
        self.assertEqual(self.state('cx-wait')['nudges'], 4)

    def test_an_unshown_question_still_blocks(self):
        session = 'cx-hidden'
        self.question_after_three_nudges(session)
        self.blocked(self.fire({**stop(session), 'last_assistant_message': 'Всё готово'}))

    def test_the_text_release_option_clears_only_after_the_human_answer(self):
        session = 'cx-release'
        self.show_question(session)
        self.fire(prompt(session, 'Закончить без ревью'))
        self.assert_silent(self.fire(stop(session)))
        self.assertEqual(self.volume(session), [])

    def test_the_review_option_starts_a_new_review_attempt(self):
        session = 'cx-review'
        self.show_question(session)
        self.fire(prompt(session, 'Провести ревью'))
        self.assertEqual(self.state(session)['nudges'], 0)
        self.assertTrue(self.volume(session))

    def test_async_receipt_is_not_a_human_answer_and_waits(self):
        session = 'cx-async'
        self.question_after_three_nudges(session)
        before = self.volume(session, every=True)
        self.fire(tool(session, 'request_user_input_async', {'questions': [{
            'title': QUESTION, 'options': ['Провести ревью', 'Закончить без ревью']}]},
            '{"accepted":true}'))
        self.assertEqual(self.volume(session, every=True), before)
        self.assertFalse(self.state(session)['released'])
        self.assert_silent(self.fire(stop(session)))

    def test_sync_choice_is_recorded_and_releases_review(self):
        session = 'cx-sync'
        self.question_after_three_nudges(session)
        self.fire(tool(session, 'request_user_input', {'questions': [{
            'id': 'review_choice', 'question': QUESTION,
            'options': [{'label': 'Провести ревью'}, {'label': 'Закончить без ревью'}]}]},
            json.dumps({'answers': {'review_choice': {'answers': ['Закончить без ревью']}}})))
        self.assertTrue(self.state(session)['released'])
        answers = [row for row in self.volume(session, every=True) if row['kind'] == 'answer']
        self.assertEqual(answers[-1]['answer'], {'answers': {QUESTION: ['Закончить без ревью']}})
        self.assert_silent(self.fire(stop(session)))

    def test_old_volume_does_not_interrupt_a_new_conversation_turn(self):
        session = 'cx-conversation'
        self.start_with_write(session)
        self.fire(prompt(session, 'Объясни текущее решение'))
        self.assert_silent(self.fire(stop(session)))
        self.assertTrue(self.volume(session))

    def test_new_write_invalidates_a_previously_shown_question(self):
        from .test_turn_review import write
        session = 'cx-new-write'
        self.show_question(session)
        self.fire(write(session))
        result = self.fire({**stop(session), 'last_assistant_message':
                            QUESTION + '\nПровести ревью\nЗакончить без ревью'})
        self.blocked(result)

    def test_async_anchor_choice_is_recorded_with_its_question(self):
        session = 'cx-anchor-async'
        self.fire(prompt(session, 'Выберем задачу'))
        self.fire(tool(session, 'request_user_input_async', {'questions': [{
            'title': 'Какой якорь у сессии?', 'options': ['Проверить установку с нуля', 'Другая задача']}]},
            '{"accepted":true}'))
        self.fire(prompt(session, '?'))
        self.assertFalse(any(row['kind'] == 'anchor' for row in self.volume(session, every=True)))
        self.fire(prompt(session, 'Проверить установку с нуля'))
        rows = self.volume(session, every=True)
        anchor = next(row for row in rows if row['kind'] == 'anchor')
        self.assertEqual(anchor['question'], 'Какой якорь у сессии?')
        self.assertEqual(anchor['answer'], 'Проверить установку с нуля')
        answer = next(row for row in rows if row['kind'] == 'answer')
        self.assertEqual(answer['question'], 'Какой якорь у сессии?')
        self.fire(prompt(session, 'Проверить установку с нуля'))
        self.assertEqual(sum(row['kind'] == 'anchor' for row in self.volume(session, every=True)), 1)

    def test_ambiguous_choice_across_questions_is_not_an_anchor(self):
        session = 'cx-ambiguous-anchor'
        self.fire(prompt(session, 'Начнём'))
        for title in ('Какой якорь?', 'Продолжить проверку?'):
            self.fire(tool(session, 'request_user_input_async', {'questions': [{
                'title': title, 'options': ['Да', 'Нет']}]}, '{"accepted":true}'))
        self.fire(prompt(session, 'Да'))
        rows = self.volume(session, every=True)
        self.assertFalse(any(row['kind'] in ('anchor', 'answer') for row in rows))
        self.assertEqual(rows[-1]['text'], 'Да')

    def test_an_unrelated_api_receipt_is_not_a_displayed_question(self):
        session = 'cx-foreign-ack'
        self.question_after_three_nudges(session)
        self.fire(tool(session, 'mcp__fake__submit_survey', {'questions': [{
            'title': QUESTION, 'options': ['Провести ревью', 'Закончить без ревью']}]},
            '{"accepted":true}'))
        self.assertFalse(self.state(session)['is_question_delivered'])
        self.blocked(self.fire(stop(session)))

    def test_an_incomplete_review_card_does_not_end_the_turn(self):
        session = 'cx-incomplete-question'
        self.question_after_three_nudges(session)
        self.fire(tool(session, 'request_user_input_async', {'questions': [{
            'title': 'Ревью хода не состоялось', 'options': ['Позже']}]}, '{"accepted":true}'))
        self.assertFalse(self.state(session)['is_question_delivered'])
        self.blocked(self.fire(stop(session)))

    def test_answer_to_an_old_question_cannot_release_new_writes(self):
        from .test_turn_review import write
        for index, choice in enumerate(('Закончить без ревью', 'закончить без ревью')):
            session = 'cx-stale-answer-' + str(index)
            self.show_question(session)
            self.fire(prompt(session, 'Подожди'))
            self.fire(write(session))
            self.fire(prompt(session, choice))
            self.assertFalse(self.state(session)['released'])
            self.blocked(self.fire(stop(session)))
            self.assertTrue(self.volume(session))

    def test_release_does_not_cover_writes_after_the_answer(self):
        from .test_turn_review import write
        session = 'cx-write-after-answer'
        self.show_question(session)
        self.fire(prompt(session, 'Закончить без ревью'))
        self.fire(write(session))
        self.blocked(self.fire(stop(session)))
        self.assertEqual(sum(row['kind'] == 'write' for row in self.volume(session)), 2)

    def test_compact_does_not_expand_an_old_question(self):
        from .test_turn_review import compact, write
        session = 'cx-compact-stale'
        self.show_question(session)
        self.fire(compact(session))
        self.fire(write(session))
        self.fire(prompt(session, 'закончить без ревью'))
        self.blocked(self.fire(stop(session)))
        self.assertTrue(self.volume(session))

    def test_new_writes_restart_automatic_review_without_reasking(self):
        from .test_turn_review import write
        session = 'cx-no-repeat-question'
        self.show_question(session)
        self.fire(prompt(session, 'Закончить без ревью'))
        self.fire(write(session))
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('Запусти подагента', reason)
        self.assertNotIn('request_user_input_async', reason)

    def test_compact_preserves_the_scope_of_an_undelivered_question(self):
        from .test_turn_review import compact, write
        session = 'cx-undelivered-compact'
        self.question_after_three_nudges(session)
        self.fire(compact(session))
        self.fire(write(session))
        self.fire(prompt(session, 'закончить без ревью'))
        self.blocked(self.fire(stop(session)))
        self.assertEqual(sum(row['kind'] == 'write' for row in self.volume(session)), 2)

    def test_a_receipt_cannot_rebind_an_old_question_to_new_writes(self):
        from .test_turn_review import write
        session = 'cx-receipt-scope'
        self.question_after_three_nudges(session)
        self.fire(write(session))
        self.fire(tool(session, 'request_user_input_async', {'questions': [{
            'title': QUESTION, 'options': ['Провести ревью', 'Закончить без ревью']}]}, '{"accepted":true}'))
        reason = self.blocked(self.fire(stop(session)))
        self.assertIn('Запусти подагента', reason)

    def test_review_choice_can_revoke_a_release_after_compact(self):
        from .test_turn_review import compact
        session = 'cx-revoke-release'
        self.show_question(session)
        self.fire(prompt(session, 'Закончить без ревью'))
        self.fire(compact(session))
        self.fire(prompt(session, 'Провести ревью'))
        self.assertFalse(self.state(session)['released'])
        self.blocked(self.fire(stop(session)))

    def test_repeated_release_answer_does_not_expand_permission(self):
        from .test_turn_review import write
        session = 'cx-repeat-release'
        self.show_question(session)
        self.fire(prompt(session, 'Закончить без ревью'))
        self.fire(write(session))
        self.fire(prompt(session, 'Закончить без ревью'))
        self.blocked(self.fire(stop(session)))
        self.assertTrue(self.volume(session))

    def test_a_legacy_release_without_a_scope_cannot_clear_new_writes(self):
        from .test_turn_review import write
        session = 'cx-legacy-release'
        self.show_question(session)
        path = self.storage(session).path('turn-review-state.json')
        state = json.loads(path.read_text())
        for key in ('question_fingerprint', 'release_fingerprint', 'is_question_delivered'):
            state.pop(key, None)
        path.write_text(json.dumps(state))
        self.fire(write(session))
        self.fire(prompt(session, 'закончить без ревью'))
        self.blocked(self.fire(stop(session)))
        self.assertEqual(sum(row['kind'] == 'write' for row in self.volume(session)), 2)
