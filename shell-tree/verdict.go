package main

import (
	"fmt"
	"sort"
	"strings"
)

// Answers to «does this command write».
const (
	writesYes     = "да"
	writesNo      = "нет"
	writesUnknown = "неизвестно"
)

// Kinds of a write target.
const (
	kindFile    = "файл"
	kindDir     = "каталог"
	kindGit     = "git"
	kindNetwork = "сеть"
	kindProcess = "процесс"
)

// Target is one place the command writes to. A place we cannot name does not
// get a target: the write itself is already in Writes and Reason, and an empty
// path in the list would read as a file called «». The one exception is the
// kind «сеть»: an outgoing command has an addressee whether or not the line
// writes it out, and there the kind is the finding.
type Target struct {
	Path string `json:"path"`
	Kind string `json:"kind"`
	Via  string `json:"via"`
	// Relative говорит, что путь дан относительно каталога вызова: каталог
	// звена неизвестен. Так бывает, когда каталог вызова не задан вовсе и когда
	// переход каталога стоял там, откуда наружу не выходит, — в подоболочке, в
	// конвейере или в условной ветке `&&`.
	Relative bool `json:"relative,omitempty"`
}

// Verdict answers whether the command line writes, where, and why we say so.
type Verdict struct {
	Writes  string   `json:"writes"`
	Targets []Target `json:"targets"`
	Reason  string   `json:"reason"`
}

// Judge answers for a parsed line. The predicate is data/shell/read-only-rules.json:
// what the data proves to read answers «нет», what it proves to change answers
// «да», and everything else answers «неизвестно». An unknown command is never
// read-only — that is the whole point of the third answer.
func Judge(tree *Tree, rules *Rules, base *Base) Verdict {
	j := &judge{rules: rules, base: base, seen: map[string]bool{}}
	j.tree(tree)
	return j.verdict()
}

// Unjudged is the verdict for a line that did not parse. Not knowing how to
// read a command is not the same as knowing it reads.
func Unjudged(err error) Verdict {
	return Verdict{Writes: writesUnknown, Reason: fmt.Sprintf("команда не разобралась: %v", err)}
}

type judge struct {
	rules   *Rules
	base    *Base
	targets []Target
	seen    map[string]bool
	yes     string // причина первого «пишет»
	unsure  string // причина первого «неизвестно»
	reading []string
}

func (j *judge) verdict() Verdict {
	out := Verdict{Targets: j.targets, Writes: writesNo}
	if out.Targets == nil {
		out.Targets = []Target{}
	}
	switch {
	case j.yes != "":
		out.Writes, out.Reason = writesYes, j.yes
	case j.unsure != "":
		out.Writes, out.Reason = writesUnknown, j.unsure
	case len(j.reading) > 0:
		out.Reason = fmt.Sprintf("только читает: %s", strings.Join(unique(j.reading), ", "))
	default:
		out.Reason = "выполнять нечего: команд в строке нет"
	}
	return out
}

func unique(list []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, item := range list {
		if seen[item] {
			continue
		}
		seen[item] = true
		out = append(out, item)
	}
	sort.Strings(out)
	return out
}

func (j *judge) writes(reason string, targets ...Target) {
	if j.yes == "" {
		j.yes = reason
	}
	for _, target := range targets {
		// Путь, оставшийся относительным, помечается здесь, в одном месте: он
		// относителен каталогу вызова, потому что каталог звена неизвестен.
		if target.Path != "" && !strings.HasPrefix(target.Path, "/") &&
			target.Kind != kindProcess && target.Kind != kindNetwork {
			target.Relative = true
		}
		key := target.Kind + "\x00" + target.Path
		if j.seen[key] {
			continue
		}
		j.seen[key] = true
		j.targets = append(j.targets, target)
	}
}

func (j *judge) unknown(reason string) {
	if j.unsure == "" {
		j.unsure = reason
	}
}

func (j *judge) tree(tree *Tree) {
	if tree == nil {
		return
	}
	for _, link := range tree.Links {
		for _, command := range link.Commands {
			j.command(command, link.Cwd)
		}
	}
}

func (j *judge) command(command Command, cwd string) {
	j.redirects(command, cwd)
	for i := range command.Substitutions {
		j.tree(&command.Substitutions[i])
	}
	// Обёртка, которой нет в списке данных, не может доказать чтение: снять её
	// мы сняли, но что она делает сверх запуска, списки не говорят.
	untrusted := ""
	for _, taken := range command.Wrappers {
		if !j.rules.isWrapper(taken.Name) {
			untrusted = taken.Name
			break
		}
	}
	unwrapped := fmt.Sprintf("обёртки «%s» нет в списке прозрачных обёрток", untrusted)
	if command.Shell != nil {
		if command.Shell.Tree == nil {
			j.unknown(fmt.Sprintf("строку оболочки «%s» разобрать не вышло: %s", command.Shell.Name, command.Shell.Error))
		} else {
			j.tree(command.Shell.Tree)
		}
		if untrusted != "" {
			j.unknown(unwrapped)
		}
		return
	}
	state, reason, targets := j.classify(command, cwd)
	if state == writesNo && untrusted != "" {
		state, reason = writesUnknown, unwrapped
	}
	switch state {
	case writesYes:
		j.writes(reason, targets...)
	case writesUnknown:
		j.unknown(reason)
	case writesNo:
		if len(command.Words) > 0 {
			j.reading = append(j.reading, command.Words[0].Text)
		}
	}
}

// redirects answers for the redirections of one command. Writing into an empty
// sink (/dev/null and its like) is not a write, and reading and here-documents
// are not writes at all.
func (j *judge) redirects(command Command, cwd string) {
	for _, redirect := range command.Redirects {
		if redirect.Mode != modeOverwrite && redirect.Mode != modeAppend {
			continue
		}
		if j.rules.isNullSink(redirect.Target.Text) {
			continue
		}
		if !redirect.Target.Literal {
			// Путь знает только оболочка: запись есть, а цели с именем нет.
			j.writes(fmt.Sprintf("перенаправление «%s» в цель, известную только оболочке: «%s»",
				redirect.Op, redirect.Target.Text))
			continue
		}
		path := resolve(cwd, redirect.Target.Text)
		j.writes(fmt.Sprintf("перенаправление «%s» в «%s»", redirect.Op, path),
			Target{Path: path, Kind: kindFile, Via: "перенаправление " + redirect.Op})
	}
}

// classify судит саму команду по спискам данных.
func (j *judge) classify(command Command, cwd string) (string, string, []Target) {
	if len(command.Words) == 0 {
		return writesNo, "", nil
	}
	if command.Construct != "" {
		return writesUnknown, fmt.Sprintf("«%s» — %s, и списки её не описывают",
			command.Words[0].Text, command.Construct), nil
	}
	head := command.Words[0]
	args := command.Words[1:]
	if !head.Literal {
		return writesUnknown, fmt.Sprintf("имя команды собирает оболочка: «%s»", head.Text), nil
	}
	name := head.Text
	if strings.Contains(name, "/") {
		return writesUnknown, fmt.Sprintf("«%s» вызвана по пути, а списки знают команды по имени", name), nil
	}
	if j.rules.isShell(name) {
		return writesUnknown, fmt.Sprintf("«%s» запускает сценарий, которого в строке нет", name), nil
	}
	if j.rules.isWrapper(name) {
		return writesUnknown, fmt.Sprintf("«%s» запускает команду, которой в строке нет", name), nil
	}
	// Редактор на месте доказывает запись сам, ключом: `perl -i` ни в базе, ни
	// в списках нет, а файл он перепишет.
	if editor, ok := inPlaceEditors[name]; ok && editor.inPlace(args) {
		return writesYes, fmt.Sprintf("«%s» правит файл на месте: ключ «-i»", name),
			editor.targets(name, args, cwd)
	}
	// Два источника предиката. Вендоренная база команд знает подкоманды с
	// оглядкой на аргументы (`git tag v1` против `git tag -l`) и ключи,
	// доказывающие запись (`sed -i`) — за ней слово о командах, которые она
	// знает. Наши списки знают команды, которых в базе нет, и держат свои
	// запрещённые ключи; строже базы они сделать ответ могут, слабее — нет.
	if effect := j.base.Effect(name, args); effect.Known {
		state, reason := effect.State, effect.Reason
		if j.rules.isMutating(name) && strength(writesYes) > strength(state) {
			state, reason = writesYes, fmt.Sprintf("«%s» из списка меняющих мир", name)
		}
		if flag, denied := j.deniedFlag(name, args); denied && strength(writesUnknown) > strength(state) {
			state, reason = writesUnknown,
				fmt.Sprintf("«%s» читает, но ключ «%s» выводит её из списка читающих", name, flag)
		}
		if state == writesYes {
			return state, reason, j.namedTargets(name, args, cwd)
		}
		return state, reason, nil
	}
	return j.byLists(name, args, cwd)
}

// deniedFlag — наши запрещённые ключи для этой команды: сама команда и её
// подкоманда. Они делают ответ строже базы, но никогда слабее.
func (j *judge) deniedFlag(name string, args []Word) (string, bool) {
	if rule, ok := j.rules.ReadOnly[name]; ok {
		if flag, denied := deniedFlag(args, rule.DenyFlags); denied {
			return flag, true
		}
	}
	if subs, ok := j.rules.DenySubcommandFlags[name]; ok {
		if sub, rest := subcommandOf(name, args); sub != "" {
			if flag, denied := deniedFlag(rest, subs[sub]); denied {
				return flag, true
			}
		}
	}
	return "", false
}

// strength orders the answers: silence about a write is the weakest thing we can
// say, a proven write the strongest.
func strength(state string) int {
	switch state {
	case writesYes:
		return 2
	case writesUnknown:
		return 1
	default:
		return 0
	}
}

// byLists — ответ по спискам data/shell/read-only-rules.json.
func (j *judge) byLists(name string, args []Word, cwd string) (string, string, []Target) {
	if j.rules.isMutating(name) {
		return writesYes, fmt.Sprintf("«%s» из списка меняющих мир", name), j.targetsOf(name, args, cwd)
	}
	if j.hasSubcommands(name) {
		return j.subcommand(name, args, cwd)
	}
	if rule, ok := j.rules.ReadOnly[name]; ok {
		if flag, denied := deniedFlag(args, rule.DenyFlags); denied {
			return writesUnknown, fmt.Sprintf("«%s» читает, но ключ «%s» выводит её из списка читающих", name, flag), nil
		}
		return writesNo, "", nil
	}
	return writesUnknown, fmt.Sprintf("«%s» нет ни в списке читающих, ни в списке меняющих мир", name), nil
}

// namedTargets собирает цели записи по нашим спискам: база команд их не знает,
// вида цели у неё нет.
func (j *judge) namedTargets(name string, args []Word, cwd string) []Target {
	if j.rules.isMutating(name) {
		return j.targetsOf(name, args, cwd)
	}
	if j.hasSubcommands(name) {
		if sub, rest := subcommandOf(name, args); sub != "" {
			return j.subTargets(name, sub, args, rest, cwd)
		}
	}
	return nil
}

func (j *judge) hasSubcommands(name string) bool {
	if _, ok := j.rules.MutatingSubcommands[name]; ok {
		return true
	}
	if _, ok := j.rules.ReadOnlySubcommands[name]; ok {
		return true
	}
	_, ok := j.rules.ReadOnlyNested[name]
	return ok
}

// subcommand judges a command that speaks in subcommands: git, npm, docker and
// their like.
func (j *judge) subcommand(name string, args []Word, cwd string) (string, string, []Target) {
	sub, rest := subcommandOf(name, args)
	if sub == "" {
		return writesUnknown, fmt.Sprintf("у «%s» не видно подкоманды, а судят её по подкоманде", name), nil
	}
	if j.rules.isMutatingSub(name, sub) {
		return writesYes, fmt.Sprintf("«%s %s» из списка меняющих мир", name, sub), j.subTargets(name, sub, args, rest, cwd)
	}
	if contains(j.rules.ReadOnlySubcommands[name], sub) {
		if flag, denied := deniedFlag(rest, j.rules.DenySubcommandFlags[name][sub]); denied {
			return writesUnknown, fmt.Sprintf("«%s %s» читает, но ключ «%s» выводит её из списка читающих", name, sub, flag), nil
		}
		return writesNo, "", nil
	}
	if nested, ok := j.rules.ReadOnlyNested[name][sub]; ok {
		if len(rest) > 0 && contains(nested, rest[0].Text) {
			return writesNo, "", nil
		}
		// Голая вложенная команда читающей не считается: `git branch` печатает
		// список, а `git stash` прячет изменения, и данные их не различают.
		return writesUnknown, fmt.Sprintf("«%s %s» читает только с ключами из списка, а тут они другие", name, sub), nil
	}
	return writesUnknown, fmt.Sprintf("«%s %s» нет ни в списке читающих, ни в списке меняющих мир", name, sub), nil
}

// subcommandOf finds the subcommand and what follows it. For git the global
// flags stand before the subcommand, and `-C` and `-c` take a value.
func subcommandOf(name string, args []Word) (string, []Word) {
	valued := map[string]bool{}
	if name == "git" {
		valued = set("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path")
	}
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if !strings.HasPrefix(arg.Text, "-") {
			if !arg.Literal {
				return "", nil
			}
			return arg.Text, args[i+1:]
		}
		if valued[arg.Text] {
			i++
		}
	}
	return "", nil
}
