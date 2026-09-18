package main

import (
	"strings"
)

// Launch wrappers: commands that take another command as their arguments and
// run it. Peeling them off is what lets the rest of the code judge the command
// that actually runs. The list is built in — it is a property of these
// programs, not of the read-only rules — and the flags are the ones that take a
// value, so the wrapped command is not mistaken for a flag's argument.
var wrappers = map[string]wrapper{
	"sudo":    {valueFlags: set("-u", "-g", "-p", "-C", "--user", "--group", "--prompt"), assignments: true},
	"doas":    {valueFlags: set("-u", "-C")},
	"env":     {valueFlags: set("-u", "--unset", "-C", "--chdir", "-S", "--split-string"), assignments: true},
	"nice":    {valueFlags: set("-n", "--adjustment")},
	"ionice":  {valueFlags: set("-c", "-n", "-p", "-P", "-u")},
	"time":    {valueFlags: set("-f", "--format", "-o", "--output")},
	"timeout": {valueFlags: set("-s", "--signal", "-k", "--kill-after"), skipOperand: 1},
	"nohup":   {},
	"stdbuf":  {valueFlags: set("-i", "-o", "-e", "--input", "--output", "--error")},
	"command": {},
	"builtin": {},
	"xargs":   {valueFlags: set("-n", "-P", "-I", "-i", "-d", "-a", "-s", "-L", "-E", "--max-args", "--max-procs", "--replace", "--delimiter", "--arg-file", "--max-chars", "--max-lines")},
}

type wrapper struct {
	valueFlags  map[string]bool
	assignments bool // `env FOO=1 cmd`, `sudo FOO=1 cmd`
	skipOperand int  // `timeout 30 cmd` — сколько своих операндов съедает обёртка
}

// Shells that take their script as a string after `-c`. Matched by the file
// name of the command and without case, because `/bin/bash -c` and `BASH -c`
// run the same shell: what we find inside the string can only add writing, so
// reading the wrapper widely is the safe side.
var shells = set("bash", "sh", "zsh", "dash", "ksh", "fish", "ash", "busybox")

// Long options of a shell that take a value; without them `bash --rcfile /tmp/rc
// -c '…'` would look like a script file and its string would stay unread.
var shellValueFlags = set("--rcfile", "--init-file")

func set(names ...string) map[string]bool {
	out := make(map[string]bool, len(names))
	for _, name := range names {
		out[name] = true
	}
	return out
}

// peel takes the launch wrappers off the front of a command and returns what is
// left plus the wrappers in the order they stood.
func peel(list []Word) ([]Word, []Wrapper) {
	var taken []Wrapper
	for len(list) > 0 {
		head := list[0]
		if !head.Literal {
			break
		}
		spec, ok := wrappers[head.Text]
		if !ok {
			break
		}
		rest, args := spec.strip(list[1:])
		if len(rest) == 0 {
			break // обёртка без команды — сама и есть команда
		}
		taken = append(taken, Wrapper{Name: head.Text, Args: args})
		list = rest
	}
	return list, taken
}

// strip walks the wrapper's own arguments and stops at the wrapped command.
func (w wrapper) strip(list []Word) ([]Word, []Word) {
	var args []Word
	operands := w.skipOperand
	for len(list) > 0 {
		head := list[0]
		switch {
		case head.Text == "--":
			args = append(args, head)
			return list[1:], args
		case strings.HasPrefix(head.Text, "-") && head.Text != "-":
			args = append(args, head)
			list = list[1:]
			if w.valueFlags[head.Text] && len(list) > 0 {
				args = append(args, list[0])
				list = list[1:]
			}
		case w.assignments && strings.Contains(head.Text, "=") && !strings.HasPrefix(head.Text, "="):
			args = append(args, head)
			list = list[1:]
		case operands > 0:
			args = append(args, head)
			list = list[1:]
			operands--
		default:
			return list, args
		}
	}
	return nil, args
}

// shell reads a `bash -c '…'` form: the shell it names and the string it runs,
// parsed as a line of its own. Nothing to read — no shell, and the command
// stays what it is.
func (b *builder) shell(list []Word) *Shell {
	if len(list) == 0 || !list[0].Literal {
		return nil
	}
	name := list[0].Text
	base := name
	if cut := strings.LastIndex(base, "/"); cut >= 0 {
		base = base[cut+1:]
	}
	if !shells[strings.ToLower(base)] {
		return nil
	}
	script, ok := scriptOf(list[1:])
	if !ok {
		return nil
	}
	// Строку разбираем и тогда, когда она известна не целиком: `bash -c "$CMD >
	// README.md"` пишет в README.md, как ни зовись подстановка. Неизвестное
	// слово останется неизвестным и внутри — читающим его никто не сочтёт.
	out := &Shell{Name: name, Script: script}
	tree, err := Parse(script.Text, b.dirs.cwd)
	if err != nil {
		out.Error = err.Error()
		return out
	}
	out.Tree = tree
	return out
}

// scriptOf finds the argument of `-c` among the shell's own options. The flag
// can stand in a cluster (`bash -lc '…'`), and `-o`/`-O` take a value of their
// own (`bash -euo pipefail -c '…'`).
func scriptOf(list []Word) (Word, bool) {
	for len(list) > 0 {
		head := list[0]
		if !strings.HasPrefix(head.Text, "-") && !strings.HasPrefix(head.Text, "+") {
			return Word{}, false // это файл сценария, а не строка
		}
		if head.Text == "--" {
			return Word{}, false
		}
		if shellValueFlags[head.Text] {
			if len(list) < 2 {
				return Word{}, false
			}
			list = list[2:]
			continue
		}
		if strings.HasPrefix(head.Text, "--") {
			list = list[1:]
			continue
		}
		cluster := head.Text[1:]
		switch {
		case strings.HasSuffix(cluster, "c"):
			if len(list) < 2 {
				return Word{}, false
			}
			return list[1], true
		case strings.HasSuffix(cluster, "o") || strings.HasSuffix(cluster, "O"):
			if len(list) < 2 {
				return Word{}, false
			}
			list = list[2:]
		default:
			list = list[1:]
		}
	}
	return Word{}, false
}
