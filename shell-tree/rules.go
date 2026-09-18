package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// rulesFile is where the data lives when --rules is not given: next to the
// binary. The data is never built into the binary — the lists are a shared
// repository file (data/shell/read-only-rules.json), and a binary carrying its
// own copy would answer by a snapshot of them.
const rulesFile = "read-only-rules.json"

// Rules is data/shell/read-only-rules.json: what counts as READING.
type Rules struct {
	ReadOnly            map[string]readOnly            `json:"readOnly"`
	ReadOnlySubcommands map[string][]string            `json:"readOnlySubcommands"`
	ReadOnlyNested      map[string]map[string][]string `json:"readOnlyNested"`
	DenySubcommandFlags map[string]map[string][]string `json:"denySubcommandFlags"`
	Wrappers            []string                       `json:"wrappers"`
	ShellWrappers       []string                       `json:"shellWrappers"`
	NullSinks           []string                       `json:"nullSinks"`
	Mutating            []string                       `json:"mutating"`
	MutatingSubcommands map[string][]string            `json:"mutatingSubcommands"`
}

type readOnly struct {
	DenyFlags []string `json:"denyFlags"`
}

// LoadRules reads the data file. An empty path means the file next to the
// binary; the error names both, so nobody has to guess where it was looked for.
func LoadRules(path string) (*Rules, error) {
	looked := path
	if path == "" {
		beside, err := besideBinary()
		if err != nil {
			return nil, err
		}
		path, looked = beside, fmt.Sprintf("%s (ключ --rules не задан)", beside)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("список «только читает» не прочитан из %s: %w", looked, err)
	}
	var rules Rules
	if err := json.Unmarshal(raw, &rules); err != nil {
		return nil, fmt.Errorf("список «только читает» из %s не разобран: %w", looked, err)
	}
	if len(rules.ReadOnly) == 0 {
		return nil, fmt.Errorf("список «только читает» из %s пуст", looked)
	}
	return &rules, nil
}

func besideBinary() (string, error) {
	self, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("не найден путь к своему бинарнику: %w", err)
	}
	if resolved, err := filepath.EvalSymlinks(self); err == nil {
		self = resolved
	}
	return filepath.Join(filepath.Dir(self), rulesFile), nil
}

func (r *Rules) isNullSink(target string) bool {
	for _, sink := range r.NullSinks {
		if target == sink {
			return true
		}
	}
	return false
}

func (r *Rules) isWrapper(name string) bool  { return contains(r.Wrappers, name) }
func (r *Rules) isShell(name string) bool    { return contains(r.ShellWrappers, name) }
func (r *Rules) isMutating(name string) bool { return contains(r.Mutating, name) }
func (r *Rules) isMutatingSub(name, sub string) bool {
	return contains(r.MutatingSubcommands[name], sub)
}

func contains(list []string, name string) bool {
	for _, item := range list {
		if item == name {
			return true
		}
	}
	return false
}

// deniedFlag finds the first argument that takes a read-only command out of the
// read-only list. A long flag matches itself and its `--flag=value` form; a
// short one matches a cluster too, because `sed -ni` writes in place exactly
// like `sed -i`, and an attached value (`sed -i.bak`) is the same flag as well.
func deniedFlag(args []Word, denied []string) (string, bool) {
	for _, arg := range args {
		if !strings.HasPrefix(arg.Text, "-") || arg.Text == "-" {
			continue
		}
		for _, flag := range denied {
			if arg.Text == flag {
				return flag, true
			}
			if strings.HasPrefix(flag, "--") {
				if strings.HasPrefix(arg.Text, flag+"=") {
					return flag, true
				}
				continue
			}
			if len(flag) != 2 || strings.HasPrefix(arg.Text, "--") {
				continue
			}
			if strings.ContainsRune(arg.Text[1:], rune(flag[1])) {
				return flag, true
			}
		}
	}
	return "", false
}
