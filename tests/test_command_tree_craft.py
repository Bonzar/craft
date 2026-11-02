"""Разбор команд Craft MCP: lib модуля command-tree-craft."""

import unittest

from jarvis import registry

from . import MODULES_DIR

SLUG = 'command-tree-craft'
TAGS = ('#задача', '#этап', '#веха', '#раздел', '#тема', '#заметка')
LOWER = 'a6784801-9d92-875c-f146-50159368745b'
UPPER = LOWER.upper()
OTHER = 'aaaaaaaa-0000-0000-0000-000000000011'


def load():
    return registry.LibraryModule(SLUG, MODULES_DIR / SLUG / 'lib').load()


craft = load()


class ToolsTest(unittest.TestCase):

    def test_the_tool_is_known_by_the_tail_of_its_name(self) -> None:
        self.assertEqual(craft.craft_tool('mcp__Craft__craft_write'), 'craft_write')
        self.assertEqual(craft.craft_tool('mcp__claude_ai_Craft__craft_read'), 'craft_read')
        self.assertEqual(craft.craft_tool('Skill'), '')
        self.assertEqual(craft.craft_tool(None), '')

    def test_write_and_read_tools(self) -> None:
        self.assertTrue(craft.is_craft_write('mcp__Craft__craft_write'))
        self.assertTrue(craft.is_craft_write('mcp__x__craft_write_and_show'))
        self.assertFalse(craft.is_craft_write('mcp__Craft__craft_read'))
        self.assertTrue(craft.is_craft_read('mcp__Craft__craft_read'))
        self.assertFalse(craft.is_craft_read('craft_read'))

    def test_command_of_takes_only_a_string(self) -> None:
        self.assertEqual(craft.command_of({'command': 'blocks get x'}), 'blocks get x')
        self.assertEqual(craft.command_of({'command': 1}), '')
        self.assertEqual(craft.command_of(None), '')


class BatchTest(unittest.TestCase):

    def test_a_batch_splits_on_semicolons_outside_quotes(self) -> None:
        text = ('blocks add --id A --markdown "раз; два" ; '
                "blocks update --id B --json '{\"markdown\":\"x; y\"}';; tasks update --id C")
        self.assertEqual(craft.split_commands(text), [
            'blocks add --id A --markdown "раз; два"',
            "blocks update --id B --json '{\"markdown\":\"x; y\"}'",
            'tasks update --id C',
        ])

    def test_an_escaped_quote_does_not_close_the_string(self) -> None:
        text = 'blocks add --markdown "он сказал \\"а; б\\"" ; blocks get X'
        self.assertEqual(craft.split_commands(text),
                         ['blocks add --markdown "он сказал \\"а; б\\""', 'blocks get X'])

    def test_the_other_quote_inside_a_string_is_text(self) -> None:
        text = "blocks add --markdown \"it's; fine\"; blocks get X"
        self.assertEqual(craft.split_commands(text),
                         ["blocks add --markdown \"it's; fine\"", 'blocks get X'])

    def test_an_empty_text_has_no_commands(self) -> None:
        self.assertEqual(craft.split_commands('  ;  '), [])


class TagsTest(unittest.TestCase):

    def test_a_tag_with_a_tail_is_the_tag(self) -> None:
        command = 'blocks add --markdown "- [x] ### Сборка #этап/готово"'
        self.assertEqual(craft.find_tags(command, TAGS), ['#этап'])

    def test_a_word_that_only_starts_like_a_tag_is_not_one(self) -> None:
        self.assertEqual(craft.find_tags('Разбор #задачами и ##задача', TAGS), [])

    def test_case_does_not_matter_and_the_answer_is_lower(self) -> None:
        self.assertEqual(craft.find_tags('Купить #Задача/5м', TAGS), ['#задача'])

    def test_tags_in_link_text_do_not_count(self) -> None:
        command = (f'- [ ] Шаг #задача [Сборка #этап](block://{LOWER}) '
                   f'[Тема #тема](craftdocs://open?blockId={LOWER})')
        self.assertEqual(craft.find_tags(command, TAGS), ['#задача'])

    def test_unicode_escapes_are_decoded_before_search(self) -> None:
        escaped = '#' + ''.join(f'\\u{ord(char):04x}' for char in 'раздел')
        self.assertEqual(craft.find_tags(f'{{"markdown":"Х {escaped}"}}', TAGS), ['#раздел'])

    def test_only_tags_of_the_given_set_are_found(self) -> None:
        self.assertEqual(craft.find_tags('#задача #проект', ['#проект']), ['#проект'])
        self.assertEqual(craft.find_tags('#задача', []), [])

    def test_a_dict_of_tags_works_as_a_set(self) -> None:
        self.assertEqual(craft.find_tags('#веха', {'#веха': 'craft-stage'}), ['#веха'])

    def test_several_tags_come_sorted_without_repeats(self) -> None:
        self.assertEqual(craft.find_tags('#этап #задача #этап', TAGS), ['#задача', '#этап'])


class IdsTest(unittest.TestCase):

    def test_block_ids_keep_order_and_drop_repeats_in_any_case(self) -> None:
        text = f'blocks get {UPPER} {OTHER} {LOWER}'
        self.assertEqual(craft.block_ids(text), [UPPER, OTHER])

    def test_is_block_id_is_the_whole_string(self) -> None:
        self.assertTrue(craft.is_block_id(LOWER))
        self.assertFalse(craft.is_block_id(f'{LOWER}x'))
        self.assertFalse(craft.is_block_id(None))

    def test_pages_of_a_read_answer_come_tags_first_then_the_json_root(self) -> None:
        markdown = f'<page id="{UPPER}">x</page><page id="not-an-id">'
        self.assertEqual(craft.answer_page_ids(markdown), [UPPER])
        self.assertEqual(craft.answer_page_ids(f'{{"id": "{OTHER}", "content": []}}'), [OTHER])
        self.assertEqual(craft.answer_page_ids('ничего'), [])


class TargetsTest(unittest.TestCase):

    def test_a_sibling_anchor_and_the_position(self) -> None:
        targets = craft.write_targets(f'blocks add --siblingId {LOWER} --position before --json {{}}')
        [anchor] = targets.anchors
        self.assertEqual((anchor.flag, anchor.block_id, anchor.role),
                         ('siblingId', LOWER, craft.Role.SIBLING))
        self.assertTrue(anchor.is_sibling)
        self.assertTrue(targets.before)
        self.assertTrue(targets.targeted)

    def test_the_container_flag_depends_on_the_verb(self) -> None:
        [add] = craft.write_targets(f'blocks add --id {LOWER} --position end').anchors
        [update] = craft.write_targets(f'blocks update --id {LOWER}').anchors
        [move] = craft.write_targets(f'blocks move --id {LOWER} --targetId {OTHER}').anchors[1:]
        self.assertTrue(add.is_container)
        self.assertEqual(update.role, craft.Role.SELF)
        self.assertTrue(move.is_container)

    def test_flags_with_equals_quotes_and_lists(self) -> None:
        targets = craft.write_targets(f'blocks delete --ids="{LOWER},{OTHER}" --position=\'before\'')
        self.assertEqual([anchor.block_id for anchor in targets.anchors], [LOWER, OTHER])
        self.assertTrue(targets.before)

    def test_values_that_are_not_ids_are_broken(self) -> None:
        targets = craft.write_targets('blocks move --id ЗАДАЧА --targetSiblingId X')
        self.assertEqual(targets.anchors, [])
        self.assertEqual(targets.broken, ["--id 'ЗАДАЧА'", "--targetSiblingId 'X'"])

    def test_a_command_without_target_flags(self) -> None:
        targets = craft.write_targets('collections items-add --items []')
        self.assertEqual((targets.anchors, targets.broken, targets.before, targets.targeted),
                         ([], [], False, False))
        self.assertTrue(craft.write_targets('tasks update --state done').targeted)

    def test_target_ids_add_json_ids(self) -> None:
        command = f'blocks update --json \'[{{"id": "{OTHER}"}}, {{"id":"{LOWER}"}}]\' --id {LOWER}'
        self.assertEqual(craft.target_ids(command), [LOWER, OTHER])

    def test_verb_is_the_first_two_words(self) -> None:
        self.assertEqual(craft.verb_of('  blocks   add --id X'), 'blocks add')
        self.assertEqual(craft.verb_of('search'), '')


class PackageTest(unittest.TestCase):

    def test_the_lib_is_loaded_once_by_slug(self) -> None:
        self.assertIs(load(), craft)


if __name__ == '__main__':
    unittest.main()
