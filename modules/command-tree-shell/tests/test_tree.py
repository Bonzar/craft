"""Двадцать форм разбора: что адаптер обязан увидеть в команде.

Формы взяты не из головы: каждая — либо место, где ручной разбор в JS
ошибался семь кругов ревью подряд (закавыченная цель, обёртка запуска, кластер
ключей, перевод строки, `cd` первым словом), либо форма из шапок трёх гвардов,
которые на это дерево переезжают.

Кейсы гоняют НАСТОЯЩИЙ `shfmt`: разбор и есть предмет проверки, и подменять его
заглушкой значило бы проверять заглушку. Нет разбора — кейсы падают, и это
верно: без него адаптер не работает вовсе.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts", "adapters"))

from adapter import call  # noqa: E402


def tree(command):
    return call({}, {"command": command})


def statements(command):
    answer = tree(command)
    assert "unsupported" not in answer, answer
    return answer["statements"]


def words(statement):
    return [w["text"] for w in statement["words"]]


class ParseForms(unittest.TestCase):

    def test_01_quoted_heredoc_body_is_not_a_command(self):
        """Тело heredoc с закавыченным маркером едет полем, а не утверждением."""
        got = statements("cat <<'EOF' > f\nrm -rf /\nEOF")
        self.assertEqual(len(got), 1)
        self.assertEqual(words(got[0]), ["cat"])
        self.assertEqual([r["op"] for r in got[0]["redirects"]], [61, 54])
        self.assertEqual(got[0]["redirects"][0]["heredoc"], "rm -rf /\n")
        self.assertEqual(got[0]["redirects"][1]["target"]["text"], "f")

    def test_02_test_clause_is_not_a_call_and_gives_no_redirect(self):
        """`[[ a > b ]]` — сравнение, а не запись: ни цели, ни имени команды."""
        got = statements("[[ a > b ]]")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "other")
        self.assertEqual(got[0]["redirects"], [])

    def test_03_quoted_write_target_is_seen(self):
        """Цель записи в кавычках видна — дыра ручного разбора, закрытая деревом."""
        got = statements('echo x > "мой файл.md"')
        target = got[0]["redirects"][0]["target"]
        self.assertEqual(target["text"], "мой файл.md")
        self.assertTrue(target["quoted"])

    def test_04_wrapper_body_stays_one_word(self):
        """Тело обёртки запуска — слово, а не разобранная команда."""
        got = statements("bash -c 'cat a && rm b'")
        self.assertEqual(words(got[0]), ["bash", "-c", "cat a && rm b"])
        self.assertTrue(got[0]["words"][2]["quoted"])

    def test_05_env_prefix_comes_as_a_word(self):
        """Присваивание окружения приходит словом `X=1` впереди остальных."""
        got = statements('env X=1 bash -c "ls"')
        self.assertEqual(words(got[0]), ["env", "X=1", "bash", "-c", "ls"])
        got = statements('LC_ALL=C.UTF-8 grep -n x README.md')
        self.assertEqual(words(got[0])[0], "LC_ALL=C.UTF-8")

    def test_06_launcher_stays_the_first_word(self):
        """Запуск от другого пользователя — обычное первое слово."""
        self.assertEqual(words(statements("sudo rm -rf /tmp/x")[0]), ["sudo", "rm", "-rf", "/tmp/x"])

    def test_07_pipe_into_xargs_is_two_statements(self):
        """Конвейер разложен на звенья, разделитель назван."""
        got = statements("pgrep -f node | xargs kill -9")
        self.assertEqual([s["sep"] for s in got], ["", "|"])
        self.assertEqual(words(got[1])[0], "xargs")

    def test_08_newline_separates_statements(self):
        """Перевод строки — самостоятельная команда, и это видно по `sep`."""
        got = statements("cat a.js\nrm -rf b")
        self.assertEqual([s["line"] for s in got], [1, 2])
        self.assertEqual([s["sep"] for s in got], ["", "\n"])
        self.assertEqual([s["line_first"] for s in got], [True, True])

    def test_09_cd_is_the_first_word_of_its_own_statement(self):
        """`cd` первым словом — свойство утверждения, а не поиск слова в строке."""
        got = statements("grep -n cd /tmp/a.txt && cat > README.md")
        self.assertEqual(words(got[0])[0], "grep")
        self.assertEqual(got[1]["redirects"][0]["target"]["text"], "README.md")
        got = statements("cd /tmp && cat > notes.md")
        self.assertEqual(words(got[0]), ["cd", "/tmp"])

    def test_10_suffix_flag_value_stays_one_word(self):
        """`-i.bak` — одно слово: правка на месте со слипшимся значением."""
        self.assertEqual(words(statements("sed -i.bak s/a/b/ README.md")[0]),
                         ["sed", "-i.bak", "s/a/b/", "README.md"])

    def test_11_short_flag_cluster_stays_one_word(self):
        """Кластер коротких ключей приходит одним словом, а не рассыпается."""
        self.assertEqual(words(statements("grep -rn образец .")[0]), ["grep", "-rn", "образец", "."])

    def test_12_substitution_is_marked_and_its_body_is_a_statement(self):
        """Подстановка помечена и текста не даёт, а её содержимое — утверждение ниже."""
        got = statements("kill -9 $(pgrep -f capgate)")
        outer = [s for s in got if s["depth"] == 0][0]
        inner = [s for s in got if s["depth"] == 1][0]
        self.assertEqual(words(inner), ["pgrep", "-f", "capgate"])
        self.assertTrue(outer["words"][2]["expanded"])
        self.assertEqual(outer["words"][2]["text"], "")

    def test_13_subshell_raises_depth(self):
        got = statements("( cd /tmp && ls )")
        self.assertEqual([s["depth"] for s in got], [1, 1])

    def test_14_loop_body_raises_depth(self):
        """Тело цикла глубже: пауза в нём — законный сторож, а не ожидание наугад."""
        got = statements("for i in 1 2 3; do sleep 10; done")
        self.assertEqual([s["depth"] for s in got], [1])
        self.assertEqual(words(got[0]), ["sleep", "10"])

    def test_15_chain_operators_are_named(self):
        got = statements("a && b || c; d")
        self.assertEqual([s["sep"] for s in got], ["", "&&", "||", ";"])

    def test_16_unresolved_variable_gives_no_text(self):
        """Нераскрытая переменная не выдумывается: слово помечено и пусто."""
        got = statements("cat $HOME/секрет.md")
        self.assertTrue(got[0]["words"][1]["expanded"])
        self.assertEqual(got[0]["words"][1]["text"], "/секрет.md")

    def test_17_unclosed_quote_is_unsupported_with_the_error(self):
        """Незакрытая кавычка — отказ С ТЕКСТОМ, а не догадка регуляркой."""
        answer = tree('cat "не закрытая кавычка')
        self.assertIn("unsupported", answer)
        self.assertTrue(answer["unsupported"].startswith("shfmt: "))
        self.assertNotIn("statements", answer)

    def test_18_empty_command_gives_no_statements(self):
        self.assertEqual(statements("   \n  "), [])
        self.assertEqual(statements(""), [])

    def test_19_missing_binary_is_unsupported_by_name(self):
        """Нет разбора — имя недостающего названо, а резки регулярками нет."""
        было = os.environ.get("SHFMT")
        os.environ["SHFMT"] = os.path.join(HERE, "нет-такого-разбора")
        try:
            answer = tree("echo x > README.md")
        finally:
            os.environ.pop("SHFMT") if было is None else os.environ.update(SHFMT=было)
        self.assertIn("unsupported", answer)
        self.assertIn("shfmt", answer["unsupported"])

    def test_20_source_travels_with_the_tree(self):
        """Сырой текст едет вместе с деревом: искомое в кавычках словом не бывает."""
        command = "ssh host 'pkill -x \"Google Chrome\"'"
        answer = tree(command)
        self.assertEqual(answer["source"], command)
        self.assertEqual(words(answer["statements"][0]), ["ssh", "host", 'pkill -x "Google Chrome"'])


if __name__ == "__main__":
    unittest.main()
