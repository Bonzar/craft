package main

import (
	"strings"
	"testing"
)

// Вердикт: три ответа и виды целей. Корпус проверяет предикат вширь, здесь —
// то, чего в корпусе нет: виды целей и то, что незнакомое не становится чтением.

func judged(t *testing.T, command, cwd string) Verdict {
	t.Helper()
	return verdictOf(command, cwd, testRules(t))
}

func TestAnUnknownCommandIsNeverReading(t *testing.T) {
	verdict := judged(t, "собери-мне-мир --быстро", "/repo")
	if verdict.Writes != writesUnknown {
		t.Errorf("writes = «%s», ждали «%s»", verdict.Writes, writesUnknown)
	}
	if verdict.Reason == "" {
		t.Error("причина пуста: по ней замок объясняет человеку, чего он не знает")
	}
}

func TestAnEmptySinkIsNotAWrite(t *testing.T) {
	if got := judged(t, "ls -la > /dev/null", "/repo").Writes; got != writesNo {
		t.Errorf("writes = «%s», ждали «нет»: /dev/null ничего не хранит", got)
	}
}

func TestAProcessIsAKindOfTarget(t *testing.T) {
	verdict := judged(t, "kill 4242", "/repo")
	if verdict.Writes != writesYes {
		t.Fatalf("writes = «%s», ждали «да»", verdict.Writes)
	}
	if !hasKind(verdict.Targets, kindProcess) {
		t.Errorf("цели %v, ждали вид «%s»", verdict.Targets, kindProcess)
	}
	if verdict.Targets[0].Path != "4242" {
		t.Errorf("цель «%s», ждали номер процесса как есть, без каталога", verdict.Targets[0].Path)
	}
}

func TestTheRepositoryIsAKindOfTarget(t *testing.T) {
	verdict := judged(t, "git commit -m x", "/repo")
	if !hasKind(verdict.Targets, kindGit) {
		t.Errorf("цели %v, ждали вид «%s»", verdict.Targets, kindGit)
	}
	if verdict.Targets[0].Path != "/repo" {
		t.Errorf("цель «%s», ждали каталог репозитория", verdict.Targets[0].Path)
	}
}

func TestTheNetworkIsAKindOfTarget(t *testing.T) {
	verdict := judged(t, "curl -o out.html https://example.invalid/x", "/repo")
	if !hasKind(verdict.Targets, kindNetwork) {
		t.Errorf("цели %v, ждали вид «%s»", verdict.Targets, kindNetwork)
	}
	if !hasKind(verdict.Targets, kindFile) {
		t.Errorf("цели %v: ответ сохраняется в файл, и файл тоже цель", verdict.Targets)
	}
}

func TestAFlagCanTakeACommandOutOfTheReadingList(t *testing.T) {
	if got := judged(t, "find . -name '*.js'", "/repo").Writes; got != writesNo {
		t.Errorf("writes = «%s», ждали «нет»", got)
	}
	if got := judged(t, "find . -name '*.js' -delete", "/repo").Writes; got != writesUnknown {
		t.Errorf("writes = «%s», ждали «%s»: ключ -delete выводит find из списка читающих", got, writesUnknown)
	}
}

func TestAWrapperOutsideTheDataCannotProveReading(t *testing.T) {
	// `sudo` разбор снимает, но в списке прозрачных обёрток его нет: доказать
	// чтение снятием того, чего данные не знают, нельзя.
	if got := judged(t, "sudo cat /etc/shadow", "/repo").Writes; got != writesUnknown {
		t.Errorf("writes = «%s», ждали «%s»", got, writesUnknown)
	}
	if got := judged(t, "timeout 30 cat README.md", "/repo").Writes; got != writesNo {
		t.Errorf("writes = «%s», ждали «нет»: timeout в списке обёрток данных", got)
	}
}

func TestAFlagValueIsNotAPath(t *testing.T) {
	verdict := judged(t, "truncate -s 0 README.md", "/repo")
	if len(verdict.Targets) != 1 || verdict.Targets[0].Path != "/repo/README.md" {
		t.Errorf("цели %v, ждали одну — /repo/README.md: «0» это значение ключа, а не файл", verdict.Targets)
	}
}

func TestABareRedirectionIsAWrite(t *testing.T) {
	// Команды нет, а файл обрезается: звено без команды пропускать нельзя.
	verdict := judged(t, "> out.txt", "/repo")
	if verdict.Writes != writesYes {
		t.Fatalf("writes = «%s», ждали «да»", verdict.Writes)
	}
	if len(verdict.Targets) != 1 || verdict.Targets[0].Path != "/repo/out.txt" {
		t.Errorf("цели %v, ждали одну — /repo/out.txt", verdict.Targets)
	}
}

func TestTheTimeKeywordIsNotTheCommand(t *testing.T) {
	// `time` — ключевое слово оболочки: судить надо то, что оно замерило.
	if got := judged(t, "time cat README.md", "/repo").Writes; got != writesNo {
		t.Errorf("writes = «%s», ждали «нет»", got)
	}
}

func TestAShellConstructSaysWhatIsNotKnown(t *testing.T) {
	verdict := judged(t, "[[ -f x ]]", "/repo")
	if verdict.Writes != writesUnknown {
		t.Errorf("writes = «%s», ждали «%s»", verdict.Writes, writesUnknown)
	}
	if !strings.Contains(verdict.Reason, "проверка") {
		t.Errorf("причина «%s» не называет конструкцию, которую разбор не раскрыл", verdict.Reason)
	}
}

func TestADeclarationIsJudgedByItsName(t *testing.T) {
	verdict := judged(t, "export FOO=1", "/repo")
	if verdict.Writes != writesUnknown {
		t.Errorf("writes = «%s», ждали «%s»", verdict.Writes, writesUnknown)
	}
	if !strings.Contains(verdict.Reason, "export") {
		t.Errorf("причина «%s» не называет команду", verdict.Reason)
	}
}

func TestAnUntrustedWrapperStaysUntrustedAroundAShell(t *testing.T) {
	// `sudo cat x` — «неизвестно», и `sudo bash -c "cat x"` обязан отвечать так
	// же: снятие обёртки, которой нет в списке данных, чтения не доказывает.
	if got := judged(t, `sudo bash -c "cat x"`, "/repo").Writes; got != writesUnknown {
		t.Errorf("writes = «%s», ждали «%s»", got, writesUnknown)
	}
	if got := judged(t, `timeout 5 bash -c "cat x"`, "/repo").Writes; got != writesNo {
		t.Errorf("writes = «%s», ждали «нет»: timeout в списке обёрток данных", got)
	}
}
