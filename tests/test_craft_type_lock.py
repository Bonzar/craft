"""Замок craft-type-lock: путь от сырого события Claude до ответа и следа."""

import json
import subprocess
import sys
import unittest

from .craft_type_lock_harness import (
    LOCK_SKILLS,
    SLUG,
    TASK_ADD,
    TASK_TEMPLATES,
    TEMPLATE,
    LockHarness,
    compact,
    prompt,
    read,
    skill,
    write,
)


class CraftTypeLockTest(LockHarness):

    def test_tagged_write_without_the_skill_is_denied_in_the_claude_form(self) -> None:
        self.fire(prompt('s-deny'))
        result = self.fire(write('s-deny', TASK_ADD))
        self.assert_denied(result, '#задача', 'craft-task', 'повтори запись')

    def test_reading_the_whole_skill_file_counts_as_calling_it(self):
        from pathlib import Path
        session = 's-file-skill'
        path = self.root / 'skills' / 'craft-task' / 'SKILL.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        text = '---\nname: craft-task\n---\nRead the template.\n'
        path.write_text(text)
        (self.root / 'installed.json').write_text(json.dumps({'source': {'state_dir': str(self.root / '.local/state/jarvis')}, 'modules': {
            'craft-task': {'parts': {'skill': str(path.parent)}}}}))
        self.fire(prompt(session))
        event = {'hook_event_name': 'PostToolUse', 'session_id': session,
                 'cwd': str(self.root), 'tool_name': 'Bash',
                 'tool_input': {'command': f'cat {path}'}, 'tool_response': text}
        self.fire(event)
        rows = [json.loads(line) for line in self.storage(session).path('turn-review-volume.jsonl').read_text().splitlines()]
        self.assertEqual(rows[-1]['skill'], 'craft-task')
        self.fire(read(session, f'blocks get {TEMPLATE["craft-task"]} --depth -1'))
        self.assert_silent(self.fire(write(session, TASK_ADD)))
        self.fire(prompt(session))
        self.fire({**event, 'tool_response': text[:15]})
        self.fire(read(session, f'blocks get {TEMPLATE["craft-task"]} --depth -1'))
        self.assert_denied(self.fire(write(session, TASK_ADD)), 'craft-task')

    def test_whole_non_entity_skill_file_is_also_in_the_review_volume(self):
        session = 's-other-file-skill'
        path = self.root / 'skills' / 'craft-snapshot' / 'SKILL.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('---\nname: craft-snapshot\n---\nRead the memory.\n')
        (self.root / 'installed.json').write_text(json.dumps({
            'source': {'state_dir': str(self.root / '.local/state/jarvis')},
            'modules': {'craft-snapshot': {'parts': {'skill': str(path.parent)}}}}))
        self.fire(prompt(session))
        self.fire({'hook_event_name': 'PostToolUse', 'session_id': session,
                   'cwd': str(self.root), 'tool_name': 'Bash',
                   'tool_input': {'command': f'cat {path}'}, 'tool_response': path.read_text()})
        rows = [json.loads(line) for line in self.storage(session).path('turn-review-volume.jsonl').read_text().splitlines()]
        self.assertEqual(rows[-1]['skill'], 'craft-snapshot')

    def test_the_skill_of_this_turn_lets_the_write_through(self) -> None:
        self.fire(prompt('s-pass'))
        self.prepare('s-pass', 'craft-task')
        self.assert_silent(self.fire(write('s-pass', TASK_ADD)))
        self.assertIn('скиллы вызваны и шаблоны прочитаны', self.trace('s-pass')[-1]['reason'])

    def test_a_plugin_prefix_is_cut_off(self) -> None:
        self.fire(prompt('s-plugin'))
        self.prepare('s-plugin', 'craft-task', 'jarvis:craft-task')
        self.assert_silent(self.fire(write('s-plugin', TASK_ADD)))
        self.assertIn('скиллы вызваны', self.last_reason('s-plugin'))

    def test_a_new_prompt_needs_the_skill_again(self) -> None:
        self.fire(prompt('s-turn'))
        self.fire(skill('s-turn', 'craft-task'))
        self.fire(prompt('s-turn'))
        self.assert_denied(self.fire(write('s-turn', TASK_ADD)), 'craft-task')

    def test_after_compact_the_skill_counts_as_not_called(self) -> None:
        self.fire(prompt('s-compact'))
        self.fire(skill('s-compact', 'craft-task'))
        self.assert_silent(self.fire(compact('s-compact')))
        self.assert_denied(self.fire(write('s-compact', TASK_ADD)), 'craft-task')

    def test_a_write_without_a_type_tag_stays_silent(self) -> None:
        self.fire(prompt('s-plain'))
        command = 'blocks update --id X --json \'{"markdown":"Просто текст #покупки"}\''
        self.assert_silent(self.fire(write('s-plain', command)))
        self.assertTrue(self.trace('s-plain')[-1]['reason'].startswith('тега типа нет'))

    def test_a_note_tag_needs_craft_note_not_another_skill(self) -> None:
        self.fire(prompt('s-note'))
        self.prepare('s-note', 'craft-task')
        command = 'blocks add --id X --markdown "Мысль про отпуск #заметка"'
        self.assert_denied(self.fire(write('s-note', command)), '#заметка', 'craft-note')
        self.prepare('s-note', 'craft-note')
        self.assert_silent(self.fire(write('s-note', command)))

    def test_a_closing_tail_on_update_is_still_the_tag(self) -> None:
        self.fire(prompt('s-done'))
        command = ('blocks update --id X --json '
                   '\'{"markdown":"- [x] Купить молоко #задача/готово","indentationLevel":1}\'')
        self.assert_denied(self.fire(write('s-done', command)), '#задача', 'craft-task')

    def test_two_types_need_both_skills(self) -> None:
        self.fire(prompt('s-two'))
        self.fire(skill('s-two', 'craft-stage'))
        command = 'blocks add --id X --markdown "- [ ] ### Сборка #этап"; ' + TASK_ADD
        result = self.fire(write('s-two', command))
        self.assert_denied(result, 'craft-task')
        reason = json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason']
        self.assertNotIn('не вызван скилл: craft-stage', reason)

    def test_a_word_that_only_starts_like_a_tag_is_not_a_tag(self) -> None:
        self.fire(prompt('s-word'))
        command = 'blocks add --id X --markdown "Список #задачами и #темами"'
        self.assert_silent(self.fire(write('s-word', command)))
        self.assertTrue(self.last_reason('s-word').startswith('тега типа нет'))

    def test_escaped_cyrillic_in_json_is_still_caught(self) -> None:
        self.fire(prompt('s-escaped'))
        escaped = json.dumps({'markdown': '- [ ] Дело #задача'})
        command = f"blocks add --id X --json '{escaped}'"
        self.assertIn('\\u0437', command)
        self.assert_denied(self.fire(write('s-escaped', command)), 'craft-task')

    def test_help_without_a_tag_stays_silent(self) -> None:
        self.fire(prompt('s-help'))
        self.assert_silent(self.fire(write('s-help', 'tasks add --help')))
        self.assertEqual(self.last_reason('s-help'), 'тега типа нет')

    def test_help_in_the_payload_does_not_open_the_lock(self) -> None:
        self.fire(prompt('s-help-payload'))
        command = 'blocks add --id X --markdown "- [ ] Учить --help #задача"'
        self.assert_denied(self.fire(write('s-help-payload', command)), 'craft-task')

    def test_a_tag_inside_link_text_does_not_count(self) -> None:
        self.fire(prompt('s-link'))
        self.prepare('s-link', 'craft-task')
        command = ('blocks add --id X --json \'{"type":"text","markdown":'
                   '"- [ ] Собрать стенд #задача/1ч [Сборка #этап](block://AB-1) '
                   '[Раздел #раздел](craftdocs://open?blockId=CD)"}\'')
        self.assert_silent(self.fire(write('s-link', command)))
        self.assertTrue(self.last_reason('s-link').startswith('теги #задача: скиллы вызваны'))

    def test_a_link_alone_is_not_a_typed_write(self) -> None:
        self.fire(prompt('s-link-only'))
        command = 'blocks add --id X --markdown "[Сборка #этап](block://AB-1)"'
        self.assert_silent(self.fire(write('s-link-only', command)))
        self.assertTrue(self.last_reason('s-link-only').startswith('тега типа нет'))

    def test_a_capitalized_tag_is_still_the_tag(self) -> None:
        self.fire(prompt('s-case'))
        command = 'blocks add --id X --markdown "- [ ] Дело #Задача и #ЭТАП"'
        result = self.fire(write('s-case', command))
        self.assert_denied(result, '#задача → craft-task', '#этап → craft-stage')

    def test_an_empty_command_stays_silent(self) -> None:
        self.fire(prompt('s-empty'))
        self.assert_silent(self.fire(write('s-empty', '   ')))
        self.assertEqual(self.last_reason('s-empty'), 'в вызове нет команды')

    def test_another_server_name_is_locked_too(self) -> None:
        self.fire(prompt('s-server'))
        result = self.fire(write('s-server', TASK_ADD, tool='mcp__claude_ai_Craft__craft_write'))
        self.assert_denied(result, 'craft-task')

    def test_a_broken_state_file_fails_loud_with_the_way_out(self) -> None:
        for session, body in (('s-broken-json', '{"turn": 1,'), ('s-broken-list', '[1, 2]')):
            path = self.storage(session).path('craft-type-lock.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding='utf-8')
            result = self.run_raw(prompt(session))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(path), result.stderr)
            self.assertIn('Удали его', result.stderr)

    def test_two_skills_called_at_once_both_count(self) -> None:
        for attempt in range(5):
            session = f's-parallel-{attempt}'
            self.fire(prompt(session))
            runs = [subprocess.Popen(
                [sys.executable, str(self.entry), '--harness', 'claude', '--event', 'post-tool'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root)},
            ) for _ in LOCK_SKILLS[:2]]
            for run, name in zip(runs, LOCK_SKILLS[:2]):
                run.stdin.write(json.dumps(skill(session, name)))
                run.stdin.close()
            for run in runs:
                self.assertEqual(run.wait(), 0, run.stderr.read())
                run.stdout.close()
                run.stderr.close()
            skills = self.storage(session).read_json('craft-type-lock.json')['skills']
            self.assertEqual(set(skills), set(LOCK_SKILLS[:2]))

    def test_another_tool_stays_silent(self) -> None:
        self.fire(prompt('s-other'))
        self.assert_silent(self.fire(write('s-other', TASK_ADD, tool='Bash')))
        self.assert_silent(self.fire(write('s-other', TASK_ADD, tool='mcp__Craft__craft_read')))

    def test_write_and_show_is_locked_too(self) -> None:
        self.fire(prompt('s-show'))
        result = self.fire(write('s-show', TASK_ADD, tool='mcp__Craft__craft_write_and_show'))
        self.assert_denied(result, 'craft-task')

    def test_a_foreign_skill_does_not_count(self) -> None:
        self.fire(prompt('s-foreign'))
        self.fire(skill('s-foreign', 'craft-call'))
        self.assert_denied(self.fire(write('s-foreign', TASK_ADD)), 'craft-task')

    def test_autonomous_run_is_locked_the_same(self) -> None:
        raw = prompt('s-auto')
        subprocess.run(
            [sys.executable, str(self.entry), '--harness', 'claude', '--event', 'prompt'],
            input=json.dumps(raw), text=True, capture_output=True, check=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root), 'JARVIS_AUTONOMOUS': '1'},
        )
        result = subprocess.run(
            [sys.executable, str(self.entry), '--harness', 'claude', '--event', 'pre-tool'],
            input=json.dumps(write('s-auto', TASK_ADD)), text=True, capture_output=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.root), 'JARVIS_AUTONOMOUS': '1'},
        )
        self.assert_denied(result, 'craft-task')
        self.assertTrue(self.trace('s-auto')[-1]['autonomous'])

    def test_the_trace_has_a_line_for_every_event(self) -> None:
        session = 's-trace'
        raws = [prompt(session), skill(session, 'craft-task'),
                read(session, f'blocks get {TEMPLATE["craft-task"]} --depth -1'), write(session, TASK_ADD),
                compact(session), write(session, TASK_ADD)]
        for raw in raws:
            self.fire(raw)
        lines = self.trace(session)
        self.assertEqual([line['event'] for line in lines],
                         ['prompt', 'post-tool', 'post-tool', 'pre-tool', 'after-compact', 'pre-tool'])
        self.assertEqual([line['response'] for line in lines],
                         ['silence', 'silence', 'silence', 'silence', 'silence', 'deny'])
        self.assertTrue(all(line['module'] == SLUG and line['reason'] for line in lines))




class TemplateLockTest(LockHarness):
    """Запись в сущность — только после чтения её шаблона в этом ходе."""

    def test_the_skill_without_the_template_is_denied_with_the_template_named(self) -> None:
        self.fire(prompt('t-skill-only'))
        self.fire(skill('t-skill-only', 'craft-task'))
        result = self.fire(write('t-skill-only', TASK_ADD))
        self.assert_denied(result, 'не прочитан шаблон #задача', f'blocks get {TASK_TEMPLATES[0]} --depth -1',
                           TASK_TEMPLATES[1], 'повтори запись')
        reason = json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason']
        self.assertNotIn('не вызван скилл', reason)

    def test_the_template_without_the_skill_is_denied(self) -> None:
        self.fire(prompt('t-read-only'))
        self.fire(read('t-read-only', f'blocks get {TASK_TEMPLATES[0]} --depth -1'))
        result = self.fire(write('t-read-only', TASK_ADD))
        self.assert_denied(result, 'не вызван скилл: craft-task')
        reason = json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason']
        self.assertNotIn('не прочитан шаблон', reason)

    def test_any_template_of_the_tag_in_any_case_will_do(self) -> None:
        for number, template in enumerate((TASK_TEMPLATES[1].upper(), TASK_TEMPLATES[0].lower())):
            session = f't-any-{number}'
            self.fire(prompt(session))
            self.fire(skill(session, 'craft-task'))
            self.fire(read(session, f'blocks get {template} --depth -1'))
            self.assert_silent(self.fire(write(session, TASK_ADD)))

    def test_a_template_of_the_last_turn_does_not_count(self) -> None:
        self.fire(prompt('t-last-turn'))
        self.fire(read('t-last-turn', f'blocks get {TASK_TEMPLATES[0]}'))
        self.fire(prompt('t-last-turn'))
        self.fire(skill('t-last-turn', 'craft-task'))
        self.assert_denied(self.fire(write('t-last-turn', TASK_ADD)), 'не прочитан шаблон #задача')

    def test_compaction_forgets_the_template(self) -> None:
        self.fire(prompt('t-compact'))
        self.prepare('t-compact', 'craft-task')
        self.fire(compact('t-compact'))
        self.fire(skill('t-compact', 'craft-task'))
        self.assert_denied(self.fire(write('t-compact', TASK_ADD)), 'не прочитан шаблон #задача')

    def test_a_template_seen_only_in_an_answer_is_not_read(self) -> None:
        self.fire(prompt('t-answer'))
        self.fire(skill('t-answer', 'craft-task'))
        raw = read('t-answer', 'search "Задача"')
        raw['tool_response'] = f'<page id="{TASK_TEMPLATES[0]}">\n</page>'
        self.fire(raw)
        self.assert_denied(self.fire(write('t-answer', TASK_ADD)), 'не прочитан шаблон #задача')

    def test_each_tag_needs_its_own_template(self) -> None:
        self.fire(prompt('t-two'))
        self.prepare('t-two', 'craft-task')
        self.fire(skill('t-two', 'craft-stage'))
        command = 'blocks add --id X --markdown "- [ ] ### Сборка #этап"; ' + TASK_ADD
        result = self.fire(write('t-two', command))
        self.assert_denied(result, 'не прочитан шаблон #этап')
        reason = json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason']
        self.assertNotIn('#задача:', reason)
        self.assertNotIn('#веха', reason)

if __name__ == '__main__':
    unittest.main()
