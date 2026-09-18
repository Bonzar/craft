package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

// The vendored bash-classify base: one YAML file per command, a snapshot of
// data/shell/commands. It answers what kind of effect a command has, and that
// is our question read backwards: READONLY means «only reads», everything else
// means «changes something», UNKNOWN means the base does not know.
const (
	classReadOnly  = "READONLY"
	classLocal     = "LOCAL_EFFECTS"
	classExternal  = "EXTERNAL_EFFECTS"
	classDangerous = "DANGEROUS"
	classUnknown   = "UNKNOWN"
)

// Severity as the base itself orders it: DANGEROUS > UNKNOWN > EXTERNAL_EFFECTS
// > LOCAL_EFFECTS > READONLY. Several options overriding at once are resolved by
// this order, the strongest wins.
var severity = map[string]int{
	classReadOnly: 0, classLocal: 1, classExternal: 2, classUnknown: 3, classDangerous: 4,
}

// commandsDir is where the base lives when --commands is not given: next to the
// binary, like the rules file. Data is never built into the binary.
const commandsDir = "commands"

// Base is the loaded command base.
type Base struct {
	byName map[string]*definition
}

// definition is one command or subcommand. The two have the same shape in the
// base, so one type serves both.
type definition struct {
	Command        string                 `yaml:"command"`
	Classification string                 `yaml:"classification"`
	Strict         *bool                  `yaml:"strict"`
	Aliases        []string               `yaml:"aliases"`
	GlobalOptions  map[string]option      `yaml:"global_options"`
	Options        map[string]option      `yaml:"options"`
	Subcommands    map[string]*definition `yaml:"subcommands"`
	SubcommandMode string                 `yaml:"subcommand_mode"`
}

type option struct {
	TakesValue        bool     `yaml:"takes_value"`
	Aliases           []string `yaml:"aliases"`
	Overrides         string   `yaml:"overrides"`
	CapturesDirectory bool     `yaml:"captures_directory"`
}

// LoadBase reads the base. An empty path means the directory next to the binary;
// the error names where it looked, so nobody has to guess.
func LoadBase(dir string) (*Base, error) {
	looked := dir
	if dir == "" {
		beside, err := besideBinaryDir()
		if err != nil {
			return nil, err
		}
		dir, looked = beside, fmt.Sprintf("%s (ключ --commands не задан)", beside)
	}
	files, err := filepath.Glob(filepath.Join(dir, "*.yaml"))
	if err != nil {
		return nil, fmt.Errorf("база команд из %s не прочитана: %w", looked, err)
	}
	if len(files) == 0 {
		return nil, fmt.Errorf("база команд из %s пуста: файлов *.yaml там нет", looked)
	}
	base := &Base{byName: make(map[string]*definition, len(files))}
	for _, file := range files {
		raw, err := os.ReadFile(file)
		if err != nil {
			return nil, fmt.Errorf("файл базы %s не прочитан: %w", file, err)
		}
		var loaded definition
		if err := yaml.Unmarshal(raw, &loaded); err != nil {
			return nil, fmt.Errorf("файл базы %s не разобран: %w", file, err)
		}
		name := loaded.Command
		if name == "" {
			name = strings.TrimSuffix(filepath.Base(file), ".yaml")
		}
		base.byName[name] = &loaded
	}
	return base, nil
}

func besideBinaryDir() (string, error) {
	self, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("не найден путь к своему бинарнику: %w", err)
	}
	if resolved, err := filepath.EvalSymlinks(self); err == nil {
		self = resolved
	}
	return filepath.Join(filepath.Dir(self), commandsDir), nil
}

func (b *Base) knows(name string) bool {
	if b == nil {
		return false
	}
	_, ok := b.byName[name]
	return ok
}

// Effect is what the base says about one command, in our words.
type Effect struct {
	State  string // да, нет, неизвестно
	Reason string
	Known  bool // база вообще знает эту команду
}

// Effect judges one command by the base. What the base does not know is left to
// the lists of read-only-rules.json.
func (b *Base) Effect(name string, args []Word) Effect {
	if !b.knows(name) {
		return Effect{}
	}
	found := b.byName[name]
	rest, overrides := stripGlobal(found, args)
	path := []string{name}
	if found.SubcommandMode == "match_all" {
		rest, path = matchAll(found, rest, path, &overrides)
	} else {
		found, rest, path = descend(found, rest, path)
	}
	unknownOption, more := scanOptions(found, rest)
	overrides = append(overrides, more...)

	base := found.Classification
	if base == "" {
		base = classReadOnly // так же читает базу и сам bash-classify
	}
	// Ключ не смешивается с базовой классификацией, а заменяет её: так читает
	// свою базу сам bash-classify. Ключей несколько — побеждает сильнейший.
	classification, by := base, ""
	for _, over := range overrides {
		if by == "" || severity[over.class] > severity[classification] {
			classification, by = over.class, over.name
		}
	}
	if strict(found) && unknownOption != "" {
		return Effect{State: writesUnknown, Known: true,
			Reason: fmt.Sprintf("«%s»: ключ «%s» базе команд незнаком, а она строга к незнакомым ключам",
				strings.Join(path, " "), unknownOption)}
	}
	reason := fmt.Sprintf("«%s»: %s по базе команд", strings.Join(path, " "), classification)
	if by != "" {
		reason = fmt.Sprintf("«%s»: ключ «%s» делает её %s по базе команд", strings.Join(path, " "), by, classification)
	}
	switch classification {
	case classReadOnly:
		return Effect{State: writesNo, Reason: reason, Known: true}
	case classUnknown:
		return Effect{State: writesUnknown, Reason: reason, Known: true}
	default:
		return Effect{State: writesYes, Reason: reason, Known: true}
	}
}

type override struct {
	name  string
	class string
}

func strict(found *definition) bool {
	return found.Strict == nil || *found.Strict
}

// stripGlobal takes the global options off the front: the base reads them only
// before the first word that is not an option.
func stripGlobal(found *definition, args []Word) ([]Word, []override) {
	var overrides []override
	if len(found.GlobalOptions) == 0 {
		return args, overrides
	}
	index := indexOptions(found.GlobalOptions)
	for i := 0; i < len(args); i++ {
		word := args[i]
		if !strings.HasPrefix(word.Text, "-") || word.Text == "-" {
			return args[i:], overrides
		}
		defined, name, attached, ok := lookup(index, word.Text)
		if !ok {
			return args[i:], overrides
		}
		if defined.Overrides != "" {
			overrides = append(overrides, override{name: name, class: defined.Overrides})
		}
		if defined.TakesValue && !attached {
			i++
		}
	}
	return nil, overrides
}

// descend walks the subcommand chain: `git`, `git stash`, `git stash push`.
func descend(found *definition, args []Word, path []string) (*definition, []Word, []string) {
	for len(args) > 0 {
		head := args[0]
		if strings.HasPrefix(head.Text, "-") || !head.Literal {
			break
		}
		next := subcommand(found, head.Text)
		if next == nil {
			break
		}
		found, args, path = next, args[1:], append(path, head.Text)
	}
	return found, args, path
}

// matchAll is the other subcommand mode: every positional is a goal of its own
// (gradle, mvn), and the strongest of them decides.
func matchAll(found *definition, args []Word, path []string, overrides *[]override) ([]Word, []string) {
	var rest []Word
	for _, word := range args {
		if strings.HasPrefix(word.Text, "-") || !word.Literal {
			rest = append(rest, word)
			continue
		}
		next := subcommand(found, word.Text)
		if next == nil {
			rest = append(rest, word)
			continue
		}
		path = append(path, word.Text)
		if next.Classification != "" {
			*overrides = append(*overrides, override{name: word.Text, class: next.Classification})
		}
	}
	return rest, path
}

func subcommand(found *definition, name string) *definition {
	if next, ok := found.Subcommands[name]; ok {
		return next
	}
	for _, next := range found.Subcommands {
		for _, alias := range next.Aliases {
			if alias == name {
				return next
			}
		}
	}
	return nil
}

// scanOptions reads the options of the matched command: what they override and
// whether any of them is unknown to the base.
func scanOptions(found *definition, args []Word) (string, []override) {
	index := indexOptions(found.Options)
	var overrides []override
	unknown := ""
	for i := 0; i < len(args); i++ {
		word := args[i]
		if !strings.HasPrefix(word.Text, "-") || word.Text == "-" || word.Text == "--" {
			continue
		}
		defined, name, attached, ok := lookup(index, word.Text)
		if !ok {
			// Незнакомый ключ запоминаем, но чтение не бросаем: `find . -name
			// x -delete` — первый ключ базе незнаком, а второй доказывает запись.
			if unknown == "" {
				unknown = name
			}
			continue
		}
		if defined.Overrides != "" {
			overrides = append(overrides, override{name: name, class: defined.Overrides})
		}
		if defined.TakesValue && !attached {
			i++
		}
	}
	return unknown, overrides
}

func indexOptions(options map[string]option) map[string]option {
	index := make(map[string]option, len(options))
	for name, defined := range options {
		index[name] = defined
		for _, alias := range defined.Aliases {
			if _, taken := index[alias]; !taken {
				index[alias] = defined
			}
		}
	}
	return index
}

// lookup finds the option a token names: itself, a `--flag=value` form, or a
// short key with the value stuck to it. The last form is how the base itself
// reads such tokens: замер 18.09.2026 — `sed -i.bak` у bash-classify это `-i`.
func lookup(index map[string]option, text string) (option, string, bool, bool) {
	if defined, ok := index[text]; ok {
		return defined, text, false, true
	}
	if cut := strings.Index(text, "="); cut > 0 {
		if defined, ok := index[text[:cut]]; ok {
			return defined, text[:cut], true, true
		}
	}
	if len(text) > 2 && text[0] == '-' && text[1] != '-' {
		if defined, ok := index[text[:2]]; ok {
			return defined, text[:2], true, true
		}
	}
	return option{}, text, false, false
}
