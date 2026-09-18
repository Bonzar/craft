package main

import "testing"

// Три дыры, которые нашёл ревью Codex на первой версии разбора, и цель у
// редактора на месте. Каждый тест падал до правки и проходит после.

func TestARedirectOfACompoundCommandIsAWrite(t *testing.T) {
	// `{ … } > result` и `( … ) > result` обрезают файл, хотя внутри только
	// читающие команды: перенаправление стоит у самой конструкции.
	for _, command := range []string{
		"{ echo x; } > result",
		"(cat input) > result",
		"for f in a b; do cat $f; done > result",
		"if true; then cat a; fi > result",
		"while read line; do echo $line; done > result",
	} {
		t.Run(command, func(t *testing.T) {
			verdict := judged(t, command, "/repo")
			if verdict.Writes != writesYes {
				t.Fatalf("writes = «%s», ждали «да»: %s", verdict.Writes, verdict.Reason)
			}
			if len(verdict.Targets) != 1 || verdict.Targets[0].Path != "/repo/result" {
				t.Errorf("цели %v, ждали одну — /repo/result", verdict.Targets)
			}
		})
	}
}

func TestASubstitutionInAHeredocBodyIsExecuted(t *testing.T) {
	// Тело незакавыченного heredoc оболочка разворачивает, и подстановка в нём
	// выполняется.
	verdict := judged(t, "cat <<EOF\n$(rm victim)\nEOF\n", "/repo")
	if verdict.Writes != writesYes {
		t.Fatalf("writes = «%s», ждали «да»: %s", verdict.Writes, verdict.Reason)
	}
	if len(verdict.Targets) != 1 || verdict.Targets[0].Path != "/repo/victim" {
		t.Errorf("цели %v, ждали одну — /repo/victim", verdict.Targets)
	}
}

func TestAQuotedHeredocBodyStaysData(t *testing.T) {
	// А закавыченный разделитель делает тело данными, и `rm` в нём — просто текст.
	verdict := judged(t, "cat <<'EOF'\n$(rm victim)\nEOF\n", "/repo")
	if verdict.Writes != writesNo {
		t.Errorf("writes = «%s», ждали «нет»: %s", verdict.Writes, verdict.Reason)
	}
}

func TestADirectoryChangeInAConditionalBranchIsNotCarried(t *testing.T) {
	// `cd` в правой части `&&` выполняется не всегда, и настоящая оболочка может
	// остаться в исходном каталоге. Назвать /tmp/victim значило бы назвать чужой
	// файл, поэтому каталог после такой ветки неизвестен.
	verdict := judged(t, "false && cd /tmp; rm victim", "/repo")
	if verdict.Writes != writesYes {
		t.Fatalf("writes = «%s», ждали «да»", verdict.Writes)
	}
	if len(verdict.Targets) != 1 || verdict.Targets[0].Path != "victim" {
		t.Errorf("цели %v, ждали «victim» как есть", verdict.Targets)
	}
	if !verdict.Targets[0].Relative {
		t.Error("цель дана относительно каталога вызова, и в вердикте это должно быть видно")
	}
}

func TestADirectoryChangeOnTheLeftOfAndIsStillCarried(t *testing.T) {
	// `cd /tmp` перед `&&` выполняется всегда: этот путь остаётся прежним.
	verdict := judged(t, "cd /tmp && rm -rf build", "/repo")
	if len(verdict.Targets) != 1 || verdict.Targets[0].Path != "/tmp/build" {
		t.Fatalf("цели %v, ждали /tmp/build", verdict.Targets)
	}
	if verdict.Targets[0].Relative {
		t.Error("каталог известен, пометка об относительности тут лишняя")
	}
}

func TestAnInPlaceEditorNamesItsFile(t *testing.T) {
	// `sed -i` пишет в файл, который стоит позиционным аргументом.
	verdict := judged(t, "sed -i s/a/b/ f", "/repo")
	if verdict.Writes != writesYes {
		t.Fatalf("writes = «%s», ждали «да»", verdict.Writes)
	}
	if len(verdict.Targets) != 1 || verdict.Targets[0].Path != "/repo/f" {
		t.Errorf("цели %v, ждали одну — /repo/f", verdict.Targets)
	}
}

func TestAnInPlaceEditorTellsTheScriptFromTheFiles(t *testing.T) {
	for command, want := range map[string][]string{
		"sed -i s/a/b/ one.txt two.txt":      {"/repo/one.txt", "/repo/two.txt"},
		"sed -i -e s/a/b/ one.txt":           {"/repo/one.txt"},
		"sed --in-place=.bak s/a/b/ one.txt": {"/repo/one.txt"},
		"perl -i -pe s/a/b/ one.txt":         {"/repo/one.txt"},
	} {
		t.Run(command, func(t *testing.T) {
			got := judged(t, command, "/repo").Targets
			if len(got) != len(want) {
				t.Fatalf("цели %v, ждали %v", got, want)
			}
			for i, path := range want {
				if got[i].Path != path {
					t.Errorf("цель %d — «%s», ждали «%s»", i, got[i].Path, path)
				}
			}
		})
	}
}

func TestASedWithoutInPlaceNamesNothing(t *testing.T) {
	verdict := judged(t, "sed -n 1,5p f", "/repo")
	if verdict.Writes != writesNo || len(verdict.Targets) != 0 {
		t.Errorf("вердикт %+v, а без -i sed только читает", verdict)
	}
}
