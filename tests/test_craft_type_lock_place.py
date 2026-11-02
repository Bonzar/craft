"""Замок craft-type-lock: место записи через connect-API и цена проверки."""

import json
import unittest

from .craft_type_lock_harness import (
    ARCHIVE,
    ARCHIVE_LINE,
    BARE_TASK,
    BULLET_JSON,
    D1,
    D2,
    DAY,
    DAY_LINE,
    DEEP_D1,
    DEEP_PAGE,
    FIRST,
    FRESH,
    FRESH_PAGE,
    LEVEL0_JSON,
    LINE,
    M_STAGE,
    M_TASK,
    MENU_LINE,
    NOTES,
    PAGE,
    PROJECT,
    PROJECT_D1,
    REPAIR,
    REQ,
    SD1,
    SECTION,
    SPHERE,
    STAGE,
    STAGE2,
    STAGE_LINK,
    TAGGED_PROJECT,
    TASK,
    TASK2,
    TASK_BODY,
    TASK_JSON,
    TOGGLE,
    UPPER_D1,
    UPPER_PAGE,
    ApiLockHarness,
    FakeCraftApi,
    compact,
    prompt,
    read,
    skill,
    write,
)


class PageTypeLockTest(ApiLockHarness):

    def test_a_write_into_the_decisions_of_a_section_needs_craft_section(self) -> None:
        self.start('p-next', PAGE)
        result = self.fire(write('p-next', f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, SECTION, '#раздел → craft-section', 'не вызван скилл: craft-section.',
                           'повтори запись')
        self.assertNotIn('craft-decisions', self.reason(result))

    def test_craft_section_this_turn_opens_the_write(self) -> None:
        self.start('p-open', PAGE)
        self.prepare('p-open', 'craft-section')
        self.assert_silent(self.fire(write('p-open', f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}')))
        self.assertIn('шаблоны прочитаны в этом ходе, цель aaaaaaaa у владельца aaaaaaaa (#раздел)',
                      self.last_reason('p-open'))

    def test_a_decision_skill_is_not_an_entity_skill(self) -> None:
        self.start('p-decision', PAGE)
        self.assert_silent(self.fire(skill('p-decision', 'craft-decisions')))
        self.assertIn('не скилл типа', self.last_reason('p-decision'))
        result = self.fire(write('p-decision', f'blocks update --id {D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_every_place_of_a_section_needs_craft_section(self) -> None:
        # Разделитель слов не несёт, а свежий блок поиск ещё не видит: их место
        # ищется в дереве прочитанной страницы.
        self.start('p-anywhere', PAGE)
        for command in (f'blocks add --siblingId {TOGGLE} --position after --json {BULLET_JSON}',
                        f'blocks add --siblingId {LINE} --position before --json {BULLET_JSON}',
                        f'blocks add --siblingId {LINE} --position after --json {BULLET_JSON}',
                        f'blocks add --siblingId {FRESH} --position after --json {BULLET_JSON}',
                        f'blocks add --siblingId {FIRST} --position before --json {BULLET_JSON}',
                        f'blocks add --id {PAGE} --position end --json {BULLET_JSON}',
                        f'blocks update --id {REQ} --json {BULLET_JSON}',
                        f'blocks delete --id {NOTES}'):
            with self.subTest(command=command):
                self.assert_denied(self.fire(write('p-anywhere', command)), SECTION, 'craft-section')

    def test_stage_decisions_need_craft_stage_from_the_project_page(self) -> None:
        self.start('p-stage', PROJECT)
        result = self.fire(write('p-stage', f'blocks add --siblingId {SD1} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, 'Запись задевает сущность «- [ ] ### Этап #этап»', '#этап → craft-stage')
        self.assertNotIn('craft-section', self.reason(result))
        self.assertNotIn('craft-project', self.reason(result))

    def test_a_document_without_a_type_tag_needs_no_skill(self) -> None:
        self.start('p-untyped', DAY)
        command = f'blocks add --siblingId {DAY_LINE} --position after --json {BULLET_JSON}'
        self.assert_silent(self.fire(write('p-untyped', command)))
        self.assertIn('тега типа нет, цель ffffffff без владельца с типом', self.last_reason('p-untyped'))

    def test_project_decisions_need_craft_project(self) -> None:
        self.start('p-tagged', TAGGED_PROJECT)
        command = f'blocks add --siblingId {PROJECT_D1} --position after --json {BULLET_JSON}'
        result = self.fire(write('p-tagged', command))
        self.assert_denied(result, 'Запись задевает сущность «Свадьба #проект»',
                           '#проект → craft-project', 'не вызван скилл: craft-project')
        self.prepare('p-tagged', 'craft-project')
        self.assert_silent(self.fire(write('p-tagged', command)))

    def test_a_link_with_a_tag_does_not_type_the_block(self) -> None:
        self.start('p-link', PROJECT)
        result = self.fire(write('p-link', f'blocks update --id {STAGE_LINK} --json {BULLET_JSON}'))
        self.assert_denied(result, 'не вызван скилл: craft-project.')
        self.assertNotIn('craft-stage', self.reason(result))

    def test_a_new_line_at_the_level_of_stages_lands_in_the_project(self) -> None:
        self.start('p-new-stage', PROJECT)
        payload = BULLET_JSON.replace('Новое решение', '- [ ] ### Новый этап #этап')
        command = f'blocks add --siblingId {TASK} --position after --json {payload}'
        result = self.fire(write('p-new-stage', command))
        self.assert_denied(result, '#этап → craft-stage', '«Проект #проект»: #проект → craft-project',
                           'не вызван скилл: craft-project, craft-stage')
        self.prepare('p-new-stage', 'craft-stage')
        self.assert_denied(self.fire(write('p-new-stage', command)), 'не вызван скилл: craft-project.')
        self.prepare('p-new-stage', 'craft-project')
        self.assert_silent(self.fire(write('p-new-stage', command)))

    def test_a_new_task_indented_under_a_stage_lands_in_the_stage(self) -> None:
        self.start('p-new-task', PROJECT)
        payload = TASK_JSON.replace('Новая задача', 'Дело')
        for command in (f'blocks add --siblingId {STAGE} --position after --json {payload}',
                        f'blocks add --siblingId {TASK} --position after --json {payload}',
                        f'blocks add --siblingId {STAGE2} --position before --json {payload}'):
            with self.subTest(command=command):
                result = self.fire(write('p-new-task', command))
                self.assert_denied(result, '«- [ ] ### Этап #этап»', 'не вызван скилл: craft-stage, craft-task')
                self.assertNotIn('craft-project', self.reason(result))

    def test_the_body_of_a_task_needs_craft_task(self) -> None:
        self.start('p-task', PROJECT)
        for command in (f'blocks add --id {TASK} --position end --json {BULLET_JSON}',
                        f'blocks update --id {TASK_BODY} --json {BULLET_JSON}',
                        f'blocks add --id {BARE_TASK} --position end --json {LEVEL0_JSON}'):
            with self.subTest(command=command):
                self.assert_denied(self.fire(write('p-task', command)), 'не вызван скилл: craft-task.')
        # Сосед задачи на её же отступе ложится к её этапу, а не в неё саму.
        result = self.fire(write('p-task', f'blocks add --siblingId {TASK} --position after --json {TASK_JSON}'))
        self.assert_denied(result, 'не вызван скилл: craft-stage, craft-task.')

    def test_closing_a_task_touches_its_stage(self) -> None:
        self.start('p-close', PROJECT)
        for command in (f'tasks update --id {TASK} --state done',
                        f'tasks update --id {BARE_TASK} --state canceled',
                        f'blocks update --id {TASK} --json \'{{"taskInfo":{{"state":"done"}},"indentationLevel":2}}\'',
                        (f'blocks update --id {BARE_TASK} --json '
                         '\'{"markdown":"- [x] Задача без тела #задача/готово","indentationLevel":2}\'')):
            with self.subTest(command=command):
                result = self.fire(write('p-close', command))
                self.assert_denied(result, 'Запись правит сущность', 'не вызван скилл: craft-stage, craft-task')
                self.assertNotIn('craft-project', self.reason(result))

    def test_retitling_or_rescheduling_a_task_touches_the_task_only(self) -> None:
        self.start('p-retitle', PROJECT)
        self.prepare('p-retitle', 'craft-task')
        for command in ((f'blocks update --id {TASK} --json '
                         '\'{"markdown":"- [ ] Новое имя #задача/1ч","indentationLevel":2}\''),
                        f'tasks update --id {TASK} --scheduleDate 2026-11-05',
                        f'blocks update --id {BARE_TASK} --markdown "- [ ] Другое имя #задача"'):
            with self.subTest(command=command):
                self.assert_silent(self.fire(write('p-retitle', command)))
                self.assertIn('без владельца с типом, правит сущность (#задача)', self.last_reason('p-retitle'))

    def test_closing_a_stage_touches_its_project(self) -> None:
        self.start('p-stage-close', PROJECT)
        command = (f'blocks update --id {STAGE2} --json '
                   '\'{"markdown":"- [x] ### Второй этап #этап/готово","indentationLevel":1}\'')
        result = self.fire(write('p-stage-close', command))
        self.assert_denied(result, 'не вызван скилл: craft-project, craft-stage')

    def test_closing_a_stage_inside_a_milestone_needs_the_stage_skill_only(self) -> None:
        # Веха — сущность того же модуля, что этап: её правила несёт тот же скилл.
        self.start('p-milestone', PROJECT)
        self.prepare('p-milestone', 'craft-stage')
        self.assert_silent(self.fire(write('p-milestone', f'tasks update --id {M_STAGE} --state done')))
        result = self.fire(write('p-milestone', f'tasks update --id {M_TASK} --state done'))
        self.assert_denied(result, '«- [ ] ### Этап вехи #этап»', 'не вызван скилл: craft-task.')

    def test_closing_a_project_touches_its_sphere(self) -> None:
        self.start('p-project-close', SPHERE)
        command = f'blocks update --id {REPAIR} --json \'{{"markdown":"Ремонт #проект/готово"}}\''
        result = self.fire(write('p-project-close', command))
        self.assert_denied(result, '«Дом #сфера»', 'не вызван скилл: craft-project, craft-sphere')

    def test_an_untagged_subpage_belongs_to_the_typed_entity_it_lies_in(self) -> None:
        self.start('p-subpages', SPHERE, PROJECT)
        cases = ((f'blocks update --id {ARCHIVE_LINE} --json {LEVEL0_JSON}', 'craft-sphere.'),
                 (f'blocks add --id {ARCHIVE} --position end --json {LEVEL0_JSON}', 'craft-sphere.'),
                 (f'blocks update --id {MENU_LINE} --json {LEVEL0_JSON}', 'craft-note.'),
                 (f'blocks add --siblingId {MENU_LINE} --position after --json {LEVEL0_JSON}', 'craft-note.'),
                 (f'blocks update --id {DEEP_D1} --json {BULLET_JSON}', 'craft-stage.'),
                 (f'blocks add --id {DEEP_PAGE} --position start --json {LEVEL0_JSON}', 'craft-stage.'))
        for command, needed in cases:
            with self.subTest(command=command):
                self.assert_denied(self.fire(write('p-subpages', command)), f'не вызван скилл: {needed}')

    def test_a_subpage_read_by_itself_still_finds_the_owner_above(self) -> None:
        self.start('p-subpage', DEEP_PAGE)
        result = self.fire(write('p-subpage', f'blocks update --id {DEEP_D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'не вызван скилл: craft-stage.')

    def test_a_sibling_of_a_read_subpage_lands_in_the_page_above(self) -> None:
        self.start('p-above', DEEP_PAGE)
        result = self.fire(write('p-above', f'blocks add --siblingId {DEEP_PAGE} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, '#этап → craft-stage')

    def test_a_tag_in_the_text_and_the_page_need_both_skills(self) -> None:
        self.start('p-both', PAGE)
        payload = BULLET_JSON.replace('Новое решение', '- [ ] Дело #задача')
        result = self.fire(write('p-both', f'blocks add --siblingId {D1} --position after --json {payload}'))
        self.assert_denied(result, '#задача → craft-task', SECTION, 'не вызван скилл: craft-section, craft-task')

    def test_an_unparsed_level_requires_every_candidate_owner(self) -> None:
        self.start('p-guess', PROJECT)
        result = self.fire(write('p-guess', f'blocks add --siblingId {TASK} --position after --json \'{{broken\''))
        self.assert_denied(result, 'не вызван скилл: craft-project, craft-stage, craft-task.')

    def test_without_reads_the_write_is_into_the_unread(self) -> None:
        self.fire(prompt('p-noread'))
        result = self.fire(write('p-noread', f'blocks add --siblingId {D1} --position after --json {BULLET_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное', 'нет чтений', 'blocks get', 'тип сущности')

    def test_a_target_outside_the_read_pages_is_into_the_unread(self) -> None:
        self.start('p-elsewhere', PROJECT)
        for target in (D1, LINE):
            with self.subTest(target=target):
                result = self.fire(write('p-elsewhere', f'blocks add --siblingId {target} --position after --json {BULLET_JSON}'))
                self.assert_denied(result, 'Запись в непрочитанное', 'не найдена среди страниц')

    def test_moving_a_task_between_stages_touches_both_stages(self) -> None:
        self.start('p-between', PROJECT)
        self.prepare('p-between', 'craft-task')
        command = f'blocks move --id {TASK2} --targetSiblingId {BARE_TASK} --position after'
        result = self.fire(write('p-between', command))
        self.assert_denied(result, '«- [ ] ### Второй этап #этап»', '«- [ ] ### Этап #этап»',
                           'не вызван скилл: craft-stage.')
        self.assertNotIn('craft-project', self.reason(result))

    def test_moving_a_task_into_a_section_needs_all_three_skills(self) -> None:
        self.start('p-move', PROJECT, PAGE)
        result = self.fire(write('p-move', f'blocks move --id {TASK} --targetSiblingId {D2} --position after'))
        self.assert_denied(result, 'не вызван скилл: craft-section, craft-stage, craft-task')

    def test_a_target_on_an_earlier_read_page_is_still_found(self) -> None:
        self.start('p-later', PAGE, PROJECT)
        self.prepare('p-later', 'craft-task')
        self.prepare('p-later', 'craft-stage')
        result = self.fire(write('p-later', f'blocks move --id {TASK} --targetSiblingId {D2} --position after'))
        self.assert_denied(result, 'не вызван скилл: craft-section.')

    def test_moving_into_a_page_is_judged_by_that_page(self) -> None:
        self.start('p-move-end', PROJECT, PAGE, DAY)
        self.prepare('p-move-end', 'craft-task')
        self.prepare('p-move-end', 'craft-stage')
        result = self.fire(write('p-move-end', f'blocks move --id {TASK} --targetId {PAGE} --position end'))
        self.assert_denied(result, 'не вызван скилл: craft-section.')
        result = self.fire(write('p-move-end', f'blocks move --id {TASK} --targetId {DAY} --position end'))
        self.assert_silent(result)

    def test_deleting_a_task_touches_its_stage(self) -> None:
        self.start('p-delete', PROJECT)
        result = self.fire(write('p-delete', f'blocks delete --id {TASK2}'))
        self.assert_denied(result, 'не вызван скилл: craft-stage, craft-task')

    def test_a_quoted_id_is_still_found(self) -> None:
        self.start('p-quoted', PAGE)
        result = self.fire(write('p-quoted', f'blocks update --id "{D1}" --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_an_unparsed_id_is_not_judged(self) -> None:
        self.start('p-broken', PAGE)
        self.assert_silent(self.fire(write('p-broken', f'blocks update --id ЗАДАЧА --json {BULLET_JSON}')))
        self.assertIn('место записи не проверено: цель в команде не разобрана', self.last_reason('p-broken'))

    def test_a_valid_target_is_judged_next_to_a_broken_one(self) -> None:
        self.start('p-mixed', PAGE)
        result = self.fire(write('p-mixed', f'blocks move --id {D1} --targetSiblingId ЗАДАЧА'))
        self.assert_denied(result, 'craft-section')

    def test_a_page_read_by_date_is_remembered_from_the_answer(self) -> None:
        self.fire(prompt('p-date'))
        raw = read('p-date', 'blocks get --date today')
        raw['tool_response'] = f'<page id="{PAGE}">\n  <pageTitle>Сегодня</pageTitle>\n</page>'
        self.assert_silent(self.fire(raw))
        result = self.fire(write('p-date', f'blocks update --id {D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_an_untagged_write_to_an_unread_target_is_denied(self) -> None:
        self.start('p-untagged', PAGE)
        result = self.fire(write('p-untagged', f'blocks update --id {TASK} --json {BULLET_JSON}'))
        self.assert_denied(result, 'Запись в непрочитанное', 'bbbbbbbb')

    def test_a_flag_after_the_payload_is_still_seen(self) -> None:
        self.start('p-tail', PAGE)
        result = self.fire(write('p-tail', f'blocks add --json {BULLET_JSON} --siblingId={D2} --position=after'))
        self.assert_denied(result, 'craft-section')

    def test_a_targeted_command_without_any_target_is_denied(self) -> None:
        self.fire(prompt('p-notarget'))
        for command in ('blocks delete', 'blocks update --json \'{"markdown":"Текст"}\'',
                        'tasks update --state done'):
            with self.subTest(command=command):
                self.assert_denied(self.fire(write('p-notarget', command)),
                                   'Цель записи не найдена', '--task', 'повтори запись')

    def test_an_unknown_place_is_announced_once_per_session(self) -> None:
        self.start('p-warn', PAGE)
        first = self.with_api_down(write('p-warn', f'blocks update --id {D1} --json {BULLET_JSON}'))
        self.fire(prompt('p-warn'))
        second = self.with_api_down(write('p-warn', f'blocks update --id {D2} --json {BULLET_JSON}'))
        text = json.loads(first.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('место записи в Craft не проверяется', text)
        self.assertIn('Тип страницы цели не проверен', text)
        self.assert_silent(second)

    def test_a_sibling_of_a_read_root_the_search_does_not_see_is_unknown(self) -> None:
        # Поиск свежую подстраницу не видит, а в её собственном дереве страницы
        # над ней нет: место не определено, и это не вина хода.
        self.start('p-rootless', FRESH_PAGE)
        result = self.fire(write('p-rootless', f'blocks add --siblingId {FRESH_PAGE} --position after --json {BULLET_JSON}'))
        text = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('страница над целью aaaaaaaa не видна', text)

    def test_a_write_before_the_first_child_of_an_untyped_document_passes(self) -> None:
        self.start('p-container-first', DAY)
        command = f'blocks add --id {DAY} --position start --json {BULLET_JSON}'
        self.assert_silent(self.fire(write('p-container-first', command)))

    def test_a_lower_case_read_of_an_upper_case_page_is_served_by_the_answer(self) -> None:
        self.fire(prompt('p-answer-case'))
        raw = read('p-answer-case', f'blocks get {UPPER_PAGE.lower()} --depth -1')
        raw['tool_response'] = f'<page id="{UPPER_PAGE}">\n</page>'
        self.assert_silent(self.fire(raw))
        result = self.fire(write('p-answer-case', f'blocks update --id {UPPER_D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_a_read_in_the_other_case_still_counts(self) -> None:
        self.start('p-retry-case', UPPER_PAGE.lower())
        result = self.fire(write('p-retry-case', f'blocks update --id {UPPER_D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_a_404_target_is_retried_in_the_other_case(self) -> None:
        self.start('p-upper', UPPER_PAGE)
        result = self.fire(write('p-upper', f'blocks update --id {UPPER_D1.lower()} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_a_corrupted_reads_list_fails_loud(self) -> None:
        self.fire(prompt('p-corrupt'))
        path = self.storage('p-corrupt').path('craft-type-lock.json')
        path.write_text('{"turn": 1, "skills": {}, "reads": [1]}', encoding='utf-8')
        result = self.fire(write('p-corrupt', 'tasks add --help'))
        self.assert_denied(result, 'испорчен', str(path), 'удали')

    def test_reads_survive_compaction(self) -> None:
        self.start('p-compact', PAGE)
        self.fire(compact('p-compact'))
        self.fire(prompt('p-compact'))
        result = self.fire(write('p-compact', f'blocks update --id {D1} --json {BULLET_JSON}'))
        self.assert_denied(result, 'craft-section')

    def test_an_unreachable_api_lets_the_write_through_with_a_warning(self) -> None:
        self.start('p-api', PAGE)
        result = self.with_api_down(write('p-api', f'blocks update --id {D1} --json {BULLET_JSON}'))
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertNotIn('permissionDecision', output)
        self.assertIn('место цели не определено', output['additionalContext'])

    def test_a_write_without_a_target_id_skips_the_api(self) -> None:
        self.fire(prompt('p-noid'))
        self.assert_silent(self.fire(write('p-noid', 'tasks add --help')))
        self.assertEqual(self.last_reason('p-noid'), 'тега типа нет')


class LookupCostTest(ApiLockHarness):
    """Цена проверки места: запросы к API на одну цель."""

    def requests_of(self, session: str, command: str) -> list[str]:
        FakeCraftApi.requests.clear()
        self.fire(write(session, command))
        return [endpoint for endpoint, _ in FakeCraftApi.requests]

    def test_a_read_typed_container_costs_one_request(self) -> None:
        self.start('c-task', TASK)
        self.assertEqual(
            self.requests_of('c-task', f'blocks add --id {TASK} --position end --json {LEVEL0_JSON}'),
            ['blocks'])

    def test_a_typed_target_edited_in_place_costs_the_block_and_the_search(self) -> None:
        self.start('c-retitle', PROJECT)
        command = f'blocks update --id {TASK} --json \'{{"markdown":"- [ ] Новое имя #задача","indentationLevel":2}}\''
        self.assertEqual(self.requests_of('c-retitle', command), ['blocks', 'blocks/search'])

    def test_an_indented_block_costs_the_block_the_search_and_the_children(self) -> None:
        self.start('c-decision', PAGE)
        self.assertEqual(
            self.requests_of('c-decision', f'blocks add --siblingId {D2} --position after --json {BULLET_JSON}'),
            ['blocks', 'blocks/search', 'blocks'])
        query = dict(FakeCraftApi.requests)['blocks/search']
        self.assertEqual(query['pattern'], r'Второе\W+решение')
        self.assertEqual((query['beforeBlockCount'], query['afterBlockCount']), ('0', '0'))
        self.assertEqual(FakeCraftApi.requests[-1][1]['maxDepth'], '1')

    def test_a_block_without_words_falls_back_to_the_read_page(self) -> None:
        # Дети страницы приходят деревом прочитанной страницы: второго запроса нет.
        self.start('c-line', PAGE)
        self.assertEqual(
            self.requests_of('c-line', f'blocks add --siblingId {LINE} --position after --json {BULLET_JSON}'),
            ['blocks', 'blocks'])
        self.assertEqual(FakeCraftApi.requests[-1][1]['maxDepth'], '-1')

if __name__ == '__main__':
    unittest.main()
