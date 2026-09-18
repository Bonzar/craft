// shell-tree — разбор командной строки оболочки в дерево и ответ «пишет ли она».
//
// Модули и замки живут на Python, а полное дерево команды из стандартной
// библиотеки Python не собрать: там есть только токены shlex. Поэтому разбор
// стоит отдельным бинарником на Go рядом с craft-sync и говорит JSON.
//
// Команда приходит на stdin:
//
//	echo 'cd /tmp && rm -rf build' | shell-tree parse   --cwd /repo
//	echo 'cd /tmp && rm -rf build' | shell-tree verdict --cwd /repo
//
// parse печатает дерево: звенья в порядке выполнения, слова после снятия
// кавычек, перенаправления, подстановки команд вложенными деревьями, снятые
// обёртки запуска и рабочий каталог каждого звена с учётом cd и pushd.
// Синтаксическая ошибка — код возврата 1, причина на stderr, на stdout ничего.
//
// verdict отвечает, пишет ли строка (да, нет, неизвестно), куда и почему.
// Предикат «только читает» лежит в data/shell/read-only-rules.json: путь к нему
// задаёт --rules, без ключа берётся файл рядом с бинарником. В бинарник данные
// не вшиты. Неразобранная команда — это «неизвестно», а не «читает».
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
)

const usage = `shell-tree — разбор команды оболочки в дерево.

  shell-tree parse   [--cwd КАТАЛОГ]
  shell-tree verdict [--cwd КАТАЛОГ] [--rules ПУТЬ]

Команда читается со stdin, ответ печатается JSON на stdout.
  --cwd    каталог вызова: от него считаются относительные пути и cd
  --rules  файл списков «только читает»; без ключа — рядом с бинарником
`

func main() {
	if err := run(os.Args[1:], os.Stdin, os.Stdout); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run(args []string, in io.Reader, out io.Writer) error {
	if len(args) == 0 {
		return fmt.Errorf("%s", usage)
	}
	mode, flags := args[0], args[1:]
	cwd, rules, err := options(flags)
	if err != nil {
		return err
	}
	source, err := io.ReadAll(in)
	if err != nil {
		return fmt.Errorf("команда со stdin не прочиталась: %w", err)
	}
	switch mode {
	case "parse":
		tree, err := Parse(string(source), cwd)
		if err != nil {
			return fmt.Errorf("команда не разобралась: %w", err)
		}
		return print(out, tree)
	case "verdict":
		loaded, err := LoadRules(rules)
		if err != nil {
			return err
		}
		tree, err := Parse(string(source), cwd)
		if err != nil {
			return print(out, Unjudged(err))
		}
		return print(out, Judge(tree, loaded))
	default:
		return fmt.Errorf("неизвестная команда «%s»\n\n%s", mode, usage)
	}
}

// options reads the flags by hand: the flag package would need a set per mode,
// and there are two of them.
func options(args []string) (string, string, error) {
	var cwd, rules string
	for i := 0; i < len(args); i++ {
		name := args[i]
		if i+1 >= len(args) {
			return "", "", fmt.Errorf("у ключа «%s» нет значения\n\n%s", name, usage)
		}
		switch name {
		case "--cwd":
			cwd = args[i+1]
		case "--rules":
			rules = args[i+1]
		default:
			return "", "", fmt.Errorf("неизвестный ключ «%s»\n\n%s", name, usage)
		}
		i++
	}
	return cwd, rules, nil
}

func print(out io.Writer, value any) error {
	encoder := json.NewEncoder(out)
	encoder.SetEscapeHTML(false) // «>» перенаправления должно остаться собой
	encoder.SetIndent("", "  ")
	return encoder.Encode(value)
}
