package main

import (
	"strings"
	"testing"
)

// Вендоренная база команд: то, ради чего она взята — подкоманда с оглядкой на
// аргументы, ключ, доказывающий запись, и команды, которых наши списки не знали.

func TestASubcommandIsJudgedByItsArguments(t *testing.T) {
	for command, want := range map[string]string{
		"git tag v1":        writesYes, // создаёт метку
		"git tag -l 'v*'":   writesNo,  // печатает список
		"git branch -a":     writesNo,
		"git branch -d old": writesYes,
		"git remote -v":     writesNo,
		"git remote add origin http://example.invalid/r.git": writesYes,
		"git worktree list":       writesNo,
		"git worktree add /tmp/w": writesYes,
		"git stash list":          writesNo,
		"git stash":               writesYes,
	} {
		t.Run(command, func(t *testing.T) {
			if got := judged(t, command, "/repo").Writes; got != want {
				t.Errorf("writes = «%s», ждали «%s»: %s", got, want, judged(t, command, "/repo").Reason)
			}
		})
	}
}

func TestAFlagThatProvesAWrite(t *testing.T) {
	// Списки знают `-i` только как ключ, выводящий sed из читающих; база знает,
	// что он и есть запись.
	for _, command := range []string{"sed -i s/a/b/ README.md", "sed -i.bak s/a/b/ README.md",
		"sed --in-place=.bak s/a/b/ README.md"} {
		t.Run(command, func(t *testing.T) {
			if got := judged(t, command, "/repo").Writes; got != writesYes {
				t.Errorf("writes = «%s», ждали «да»", got)
			}
		})
	}
	if got := judged(t, "sed -n 1,5p README.md", "/repo").Writes; got != writesNo {
		t.Errorf("writes = «%s», ждали «нет»: без -i sed только читает", got)
	}
}

func TestTheListsStillAnswerForWhatTheBaseDoesNotKnow(t *testing.T) {
	// `bat`, `od`, `cksum` в базе команд отсутствуют, а в наших списках есть.
	for _, command := range []string{"bat /repo/a.js", "od /repo/a.js", "cksum /repo/a.js"} {
		t.Run(command, func(t *testing.T) {
			if got := judged(t, command, "/repo").Writes; got != writesNo {
				t.Errorf("writes = «%s», ждали «нет»", got)
			}
		})
	}
}

func TestTheListsCanOnlyMakeTheAnswerStricter(t *testing.T) {
	// База собрана здесь, а не взята с диска: проверяется само правило слияния,
	// а живая база про `tee` говорит то же, что и списки, и правило осталось бы
	// непроверенным.
	soft := &Base{byName: map[string]*definition{
		"tee": {Command: "tee", Classification: classReadOnly},
	}}
	tree, err := Parse("tee out.txt", "/repo")
	if err != nil {
		t.Fatal(err)
	}
	verdict := Judge(tree, testRules(t), soft)
	if verdict.Writes != writesYes {
		t.Errorf("writes = «%s», ждали «да»: список меняющих мир строже базы", verdict.Writes)
	}
	if len(verdict.Targets) != 1 || verdict.Targets[0].Path != "/repo/out.txt" {
		t.Errorf("цели %v, ждали /repo/out.txt: цель берётся из наших списков", verdict.Targets)
	}
}

func TestAnUnknownCommandStaysUnknownInBothSources(t *testing.T) {
	if got := judged(t, "craft-sync --backlinks abc", "/repo").Writes; got != writesUnknown {
		t.Errorf("writes = «%s», ждали «%s»", got, writesUnknown)
	}
}

func TestTheBaseNamesWhereItLookedWhenItIsMissing(t *testing.T) {
	_, err := LoadBase("нет-такого-каталога")
	if err == nil {
		t.Fatal("без базы команд вердикта быть не может")
	}
	if !strings.Contains(err.Error(), "нет-такого-каталога") {
		t.Errorf("ошибка «%v» не называет каталог, которого не хватило", err)
	}
}

func TestTheWholeBaseLoads(t *testing.T) {
	// Файл базы, который не разобрался, — это молча потерянная команда.
	base, err := LoadBase("../data/shell/commands")
	if err != nil {
		t.Fatalf("база не прочитана: %v", err)
	}
	if len(base.byName) < 150 {
		t.Errorf("команд в базе %d, а их должно быть больше 150", len(base.byName))
	}
	for _, name := range []string{"git", "sed", "find", "rm", "curl", "docker", "npm"} {
		if !base.knows(name) {
			t.Errorf("в базе нет «%s»", name)
		}
	}
}
