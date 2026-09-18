package main

import (
	"bytes"
	"strings"
	"testing"
)

// Дерево: то, ради чего разбор стоит отдельным бинарником. Проверяется не форма
// JSON, а то, что из строки видно — слова, цели, каталоги и вложенные строки.

func parsed(t *testing.T, command, cwd string) *Tree {
	t.Helper()
	tree, err := Parse(command, cwd)
	if err != nil {
		t.Fatalf("команда «%s» не разобралась: %v", command, err)
	}
	return tree
}

func textsOf(words []Word) []string {
	out := make([]string, 0, len(words))
	for _, word := range words {
		out = append(out, word.Text)
	}
	return out
}

func equal(got, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range got {
		if got[i] != want[i] {
			return false
		}
	}
	return true
}

func TestQuotesComeOffTheWords(t *testing.T) {
	tree := parsed(t, `grep -n "две слова" 'один файл.txt'`, "")
	got := textsOf(tree.Links[0].Commands[0].Words)
	want := []string{"grep", "-n", "две слова", "один файл.txt"}
	if !equal(got, want) {
		t.Errorf("слова %q, ждали %q", got, want)
	}
	for _, word := range tree.Links[0].Commands[0].Words {
		if !word.Literal {
			t.Errorf("слово «%s» должно быть буквальным: кавычки снимаются, а не делают слово неизвестным", word.Text)
		}
	}
}

func TestAWordWithAParameterIsNotLiteral(t *testing.T) {
	tree := parsed(t, `cat $HOME/секрет.md`, "/repo")
	word := tree.Links[0].Commands[0].Words[1]
	if word.Literal {
		t.Errorf("слово «%s» не может быть буквальным: путь знает только оболочка", word.Text)
	}
	if !strings.Contains(word.Text, "$HOME") {
		t.Errorf("в слове «%s» потерялась подстановка: показывать надо то, что написано", word.Text)
	}
}

func TestAHeredocKeepsItsBodyAndIsNotACommand(t *testing.T) {
	tree := parsed(t, "cat <<'EOF' > f.txt\nrm -rf /\nEOF\n", "/repo")
	command := tree.Links[0].Commands[0]
	if len(command.Redirects) != 2 {
		t.Fatalf("перенаправлений %d, ждали два: heredoc и запись в файл", len(command.Redirects))
	}
	heredoc := command.Redirects[0]
	if heredoc.Mode != modeHeredoc {
		t.Errorf("режим «%s», ждали «%s»", heredoc.Mode, modeHeredoc)
	}
	if !strings.Contains(heredoc.Body, "rm -rf /") {
		t.Errorf("тело heredoc потеряно: %q", heredoc.Body)
	}
	if len(tree.Links) != 1 {
		t.Errorf("звеньев %d, ждали одно: тело heredoc — это данные, а не команды", len(tree.Links))
	}
}

func TestANestedShellStringIsParsedOnItsOwn(t *testing.T) {
	tree := parsed(t, `bash -c 'cd /tmp && cat > inner.txt'`, "/repo")
	shell := tree.Links[0].Commands[0].Shell
	if shell == nil || shell.Tree == nil {
		t.Fatalf("строка оболочки не разобрана: %+v", shell)
	}
	if shell.Name != "bash" {
		t.Errorf("оболочка «%s», ждали bash", shell.Name)
	}
	inner := shell.Tree.Links
	if len(inner) != 2 {
		t.Fatalf("во вложенном дереве %d звеньев, ждали два", len(inner))
	}
	if inner[1].Cwd != "/tmp" {
		t.Errorf("каталог второго звена «%s», ждали /tmp: cd внутри строки считается", inner[1].Cwd)
	}
	if got := inner[1].Commands[0].Redirects[0].Target.Text; got != "inner.txt" {
		t.Errorf("цель перенаправления «%s», ждали inner.txt", got)
	}
}

func TestADirectoryChangeMovesTheLinksAfterIt(t *testing.T) {
	tree := parsed(t, "cd a && rm -rf b", "/repo")
	if got := tree.Links[0].Cwd; got != "/repo" {
		t.Errorf("каталог первого звена «%s», ждали /repo", got)
	}
	if got := tree.Links[1].Cwd; got != "/repo/a" {
		t.Errorf("каталог второго звена «%s», ждали /repo/a", got)
	}
	verdict := Judge(tree, testRules(t), loadedBase)
	if len(verdict.Targets) != 1 || verdict.Targets[0].Path != "/repo/a/b" {
		t.Errorf("цели %v, ждали одну — /repo/a/b: относительный путь читается от каталога звена", verdict.Targets)
	}
	if verdict.Targets[0].Kind != kindDir {
		t.Errorf("вид цели «%s», ждали «%s»: у `rm -rf` цель — каталог", verdict.Targets[0].Kind, kindDir)
	}
}

func TestADirectoryChangeInASubshellStaysThere(t *testing.T) {
	tree := parsed(t, "(cd /tmp) && rm b", "/repo")
	last := tree.Links[len(tree.Links)-1]
	if last.Cwd != "/repo" {
		t.Errorf("каталог последнего звена «%s», ждали /repo: cd в подоболочке наружу не выходит", last.Cwd)
	}
}

func TestADirectoryChangeItCannotReadMakesTheDirectoryUnknown(t *testing.T) {
	tree := parsed(t, "cd /repo/$SUB && rm b", "/repo")
	last := tree.Links[len(tree.Links)-1]
	if last.Cwd != "" {
		t.Errorf("каталог «%s», а он неизвестен: `cd` с подстановкой читать нечем", last.Cwd)
	}
	verdict := Judge(tree, testRules(t), loadedBase)
	if len(verdict.Targets) != 1 || verdict.Targets[0].Path != "b" {
		t.Errorf("цели %v, ждали «b» как есть: корень выдумывать нельзя", verdict.Targets)
	}
}

func TestALaunchWrapperComesOff(t *testing.T) {
	tree := parsed(t, "sudo -u root rm -rf /tmp/x", "/repo")
	command := tree.Links[0].Commands[0]
	if len(command.Wrappers) != 1 || command.Wrappers[0].Name != "sudo" {
		t.Fatalf("обёртки %+v, ждали одну — sudo", command.Wrappers)
	}
	if got := textsOf(command.Wrappers[0].Args); !equal(got, []string{"-u", "root"}) {
		t.Errorf("аргументы обёртки %q, ждали -u root", got)
	}
	if got := textsOf(command.Words); !equal(got, []string{"rm", "-rf", "/tmp/x"}) {
		t.Errorf("слова после снятия обёртки %q, ждали «rm -rf /tmp/x»", got)
	}
}

func TestAPipelineIsOneLinkWithItsRedirect(t *testing.T) {
	tree := parsed(t, "cat a.txt | grep x > out.txt", "/repo")
	if len(tree.Links) != 1 {
		t.Fatalf("звеньев %d, ждали одно: конвейер — это звено", len(tree.Links))
	}
	if len(tree.Links[0].Commands) != 2 {
		t.Fatalf("команд в звене %d, ждали две", len(tree.Links[0].Commands))
	}
	redirects := tree.Links[0].Commands[1].Redirects
	if len(redirects) != 1 || redirects[0].Mode != modeOverwrite {
		t.Fatalf("перенаправление второго звена %+v, ждали перезапись", redirects)
	}
	if redirects[0].Target.Text != "out.txt" {
		t.Errorf("цель «%s», ждали out.txt", redirects[0].Target.Text)
	}
}

func TestACommandSubstitutionIsATreeOfItsOwn(t *testing.T) {
	tree := parsed(t, `cat "$(rm -f x && echo y)"`, "/repo")
	subs := tree.Links[0].Commands[0].Substitutions
	if len(subs) != 1 {
		t.Fatalf("подстановок %d, ждали одну", len(subs))
	}
	if len(subs[0].Links) != 2 {
		t.Fatalf("во вложенном дереве %d звеньев, ждали два", len(subs[0].Links))
	}
	if got := textsOf(subs[0].Links[0].Commands[0].Words); !equal(got, []string{"rm", "-f", "x"}) {
		t.Errorf("слова подстановки %q, ждали «rm -f x»", got)
	}
	verdict := Judge(tree, testRules(t), loadedBase)
	if verdict.Writes != writesYes {
		t.Errorf("writes = «%s», ждали «да»: запись внутри подстановки — это запись", verdict.Writes)
	}
}

func TestABackgroundLinkIsMarked(t *testing.T) {
	tree := parsed(t, "cat a.js & cat b.js", "")
	if !tree.Links[0].Background {
		t.Error("первое звено ушло в фон, а в дереве это не видно")
	}
	if tree.Links[1].Background {
		t.Error("второе звено в фон не уходило")
	}
}

func TestTheOperatorsBetweenLinksAreKept(t *testing.T) {
	tree := parsed(t, "a; b && c || d", "")
	want := []string{"", ";", "&&", "||"}
	for i, op := range want {
		if tree.Links[i].Op != op {
			t.Errorf("звено %d связано «%s», ждали «%s»", i, tree.Links[i].Op, op)
		}
	}
}

func TestASyntaxErrorIsAnError(t *testing.T) {
	if _, err := Parse(`cat "не закрытая кавычка`, ""); err == nil {
		t.Error("незакрытая кавычка разобралась: так разбор врёт о том, что будет выполнено")
	}
}

// Вход и выход бинарника: синтаксическая ошибка — не ноль, на stdout ничего.
func TestParseModeSaysNothingOnABrokenCommand(t *testing.T) {
	var out bytes.Buffer
	err := run([]string{"parse"}, strings.NewReader(`cat "не закрытая`), &out)
	if err == nil {
		t.Fatal("разбор кривой команды обязан кончиться ошибкой")
	}
	if out.Len() != 0 {
		t.Errorf("на stdout ушло %q, а должно быть пусто", out.String())
	}
}

func TestVerdictModeAnswersUnknownOnABrokenCommand(t *testing.T) {
	var out bytes.Buffer
	err := run([]string{"verdict", "--rules", "../data/shell/read-only-rules.json",
		"--commands", "../data/shell/commands"}, strings.NewReader(`cat "не закрытая`), &out)
	if err != nil {
		t.Fatalf("вердикт обязан ответить, а не упасть: %v", err)
	}
	if !strings.Contains(out.String(), writesUnknown) {
		t.Errorf("ответ %q, а неразобранная команда — это «%s»", out.String(), writesUnknown)
	}
}

func TestVerdictModeNamesTheMissingRules(t *testing.T) {
	var out bytes.Buffer
	err := run([]string{"verdict", "--rules", "нет-такого-файла.json",
		"--commands", "../data/shell/commands"}, strings.NewReader("ls"), &out)
	if err == nil {
		t.Fatal("без списков вердикта быть не может")
	}
	if !strings.Contains(err.Error(), "нет-такого-файла.json") {
		t.Errorf("ошибка «%v» не называет файл, которого не хватило", err)
	}
}
