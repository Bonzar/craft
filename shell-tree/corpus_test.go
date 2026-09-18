package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
)

// Корпус старого разбора: data/shell/write-targets-cases.json, 233 кейса,
// снятые с тестов снятого слоя JS-хуков. Поле `expected` там — ожидание своими
// словами, и переводится в утверждения о нашем ответе оно ЗДЕСЬ, явной
// таблицей: без неё «прошло» значило бы только «не упало».
//
// Перевод по семьям фраз:
//
//	«только читает — доказано»        → writes == «нет»
//	«только читает — не доказано»     → writes != «нет» (да или неизвестно)
//	«мир не менялся»                  → writes == «нет»
//	«мир менялся»                     → writes == «да»
//	«отправка (push)»                 → writes == «да» и цель вида «сеть»
//	«не отправка»                     → целей вида «сеть» нет
//	«цель записи — X»                 → writes == «да» и X в списке целей
//	«…, один раз»                     → X в списке ровно один раз
//	«целей записи нет»                → список целей пуст
//	«запись известна»                 → writes == «да»
//	«запись НЕ известна»              → writes == «неизвестно»
//	«записи нет»                      → writes == «нет»
//	«виновник — «X»»                  → причина называет X
//	«прочитан …», «чтение»            → writes == «нет», но только когда фраза
//	                                    ничего не сказала про запись: у кейсов
//	                                    вроде «не чтение, запись известна»
//	                                    решает часть про запись
//
// Фразы про прочитанные файлы — это ожидания другого гварда (чтение до записи),
// и про запись они говорят лишь косвенно: читающая команда не пишет.
const casesPath = "../data/shell/write-targets-cases.json"

type corpusCase struct {
	Command  string `json:"command"`
	Expected string `json:"expected"`
	Why      string `json:"why"`
}

// claim — что мы утверждаем о вердикте по фразе кейса.
type claim struct {
	writes    string
	notWrites string
	target    string
	once      bool
	noTargets bool
	network   bool
	noNetwork bool
	blames    string
}

func (c claim) empty() bool { return c == claim{} }

// Кейсы, которые не сошлись. Каждый — с причиной, и та же причина в отчёте.
// Подгонять код или данные под них нельзя: расхождение это находка, а не шум.
var skipped = map[string]string{
	"git branch|мир не менялся":                                       "данные описывают `git branch` только через readOnlyNested со списком ключей; голого `git branch` в списке нет, и вердикт — «неизвестно». Считать голую вложенную команду читающей нельзя: голый `git stash` прячет изменения.",
	"git stash|мир менялся":                                           "`git stash` не значится ни в meняющих подкомандах, ни в читающих: вердикт — «неизвестно», а кейс ждёт «да».",
	"git stash push -m wip|мир менялся":                               "то же: `git stash push` данные не знают, вердикт — «неизвестно».",
	"git branch feature|мир менялся":                                  "`git branch` с именем ветки в readOnlyNested не попадает и в меняющих подкомандах не значится: вердикт — «неизвестно».",
	"git branch -d old|мир менялся":                                   "то же: ключ `-d` не в списке читающих, а в меняющих подкомандах `branch` нет: вердикт — «неизвестно».",
	"git tag v1|мир менялся":                                          "ДЕФЕКТ ДАННЫХ: `tag` лежит в readOnlySubcommands.git без оглядки на аргументы, и `git tag v1` объявляется читающим. Кейс прав, данные — нет.",
	"git log --oneline | head; git tag v1|мир менялся":                "тот же дефект данных: `git tag v1` во втором звене объявляется читающим.",
	"git remote add origin https://example.invalid/r.git|мир менялся": "ДЕФЕКТ ДАННЫХ: `remote` лежит в readOnlySubcommands.git без оглядки на аргументы, и `git remote add` объявляется читающим.",
	"git push --dry-run|не отправка":                                  "данные про `--dry-run` ничего не знают: `git push` в меняющих подкомандах, и вердикт называет цель вида «сеть».",
	"echo x > /tmp/scratch.txt|мир не менялся":                        "старый гвард не считал /tmp миром. Наш предикат — «пишет ли команда», и запись в /tmp это запись; сужение до репозитория — дело замка, а не разбора.",
	"printf x > /tmp/черновик|целей записи нет":                       "то же сужение до репозитория: цель /tmp/черновик мы называем.",
	"git worktree list|мир не менялся":                                "ДЕФЕКТ ДАННЫХ наоборот: `worktree` лежит в mutatingSubcommands.git без оглядки на аргументы, и читающий `git worktree list` объявляется пишущим. Лишний вопрос человеку, но не дыра.",
	"sed -i.bak s/a/b/ /repo/README.md|не чтение, запись известна, прочитанных файлов нет":                                    "данные знают `-i` только как ключ, выводящий `sed` из списка читающих; доказательства записи в них нет, и вердикт — «неизвестно».",
	"sed --in-place=.bak s/a/b/ /repo/README.md|не чтение, запись известна, прочитанных файлов нет":                           "то же: `--in-place` выводит из списка читающих, но записи не доказывает.",
	"cat a.js\nsed -i s/a/b/ /repo/README.md|только читает — не доказано; не чтение, запись известна, прочитанных файлов нет": "то же: «только читает — не доказано» сходится, а «запись известна» — нет, данные записи `sed -i` не доказывают.",
	"cat <<'EOF'|прочитанных файлов нет": "незакрытый heredoc mvdan/sh считает синтаксической ошибкой (bash его принимает с предупреждением). Вердикт — «неизвестно», а не «читает».",
}

// Кейсы, где фраза говорит не про запись или говорит про другой разбор.
// Утверждение задано руками, и рядом — почему.
var overrides = map[string]claim{
	// «mutates» — код причины старого гварда; у нас та же причина словами.
	"rm README.md|причина отказа — «mutates»": {writes: writesYes, blames: "rm"},
	// Обёртка не снимается: `perl` нет ни в списке читающих, ни в меняющих.
	`perl -c "cat a.js"|обёртка не снимается, команда остаётся как есть`: {writes: writesUnknown},
	// Строка после `perl -c` остаётся аргументом, целей записи из неё нет.
	`perl -c "cat > README.md"|целей записи нет`: {writes: writesUnknown, noTargets: true},
	// «Адаптеров команды» в новой системе нет: цель мы называем сразу.
	"printf x > /repo/out.txt|без адаптера команды — целей нет, недостающая возможность названа «write-targets»": {writes: writesYes, target: "/repo/out.txt"},
	// `bash deploy.sh` — это сценарий, а не строка: что внутри, неизвестно.
	`bash deploy.sh -c "echo x > README.md"|целей записи нет`: {writes: writesUnknown, noTargets: true},
	// `echo` печатает свои аргументы: ни записи, ни целей.
	`echo bash -c "x > y"|целей записи нет`: {writes: writesNo, noTargets: true},
	// Цель перенаправления известна только оболочке: пишет, а назвать нечего.
	`bash -c "cat > $OUT"|целей записи нет`: {writes: writesYes, noTargets: true},
	// Старый разбор снимал одну обёртку и до `deep.md` не доходил, наш снимает
	// все: цель находится. Утверждаем то, в чём сходимся, — что чтение не
	// доказано; лишняя найденная цель названа в отчёте этапа.
	`bash -c 'bash -c "cat > deep.md"'|целей записи нет, но «только читает» — не доказано`: {notWrites: writesNo},
}

// claimOf переводит фразу кейса в утверждения. Про запись и про цели фразы
// говорят по отдельности, и читаются они тоже по отдельности.
func claimOf(expected string) claim {
	out, said := writesClaim(expected)
	said = targetsClaim(expected, &out) || said
	if cut := strings.Index(expected, "виновник — «"); cut >= 0 {
		name := expected[cut+len("виновник — «"):]
		if end := strings.Index(name, "»"); end >= 0 {
			out.blames = name[:end]
			said = true
		}
	}
	// Фразы про прочитанные файлы — ожидания другого гварда. Про запись они
	// говорят лишь тем, что читающая команда не пишет, и слушаем мы их только
	// тогда, когда про запись не сказано ничего.
	if !said && (strings.HasPrefix(expected, "прочитан") || strings.HasPrefix(expected, "чтение") ||
		strings.HasPrefix(expected, "снятие обёртки")) {
		out.writes = writesNo
	}
	return out
}

func writesClaim(expected string) (claim, bool) {
	var out claim
	said := false
	switch {
	case strings.Contains(expected, "только читает — доказано"):
		out.writes, said = writesNo, true
	case strings.Contains(expected, "только читает") && strings.Contains(expected, "не доказано"):
		out.notWrites, said = writesNo, true
	}
	if strings.Contains(expected, "мир не менялся") {
		out.writes, said = writesNo, true
	} else if strings.Contains(expected, "мир менялся") {
		out.writes, said = writesYes, true
	}
	// «записи нет» ищется отдельной фразой, а не куском «целей записи нет»:
	// иначе «мир менялся, целей записи нет» читалось бы как «не пишет».
	if strings.Contains(expected, "запись НЕ известна") {
		out.writes, said = writesUnknown, true
	} else if strings.Contains(expected, "запись известна") {
		out.writes, said = writesYes, true
	} else if strings.HasPrefix(expected, "записи нет") || strings.Contains(expected, ", записи нет") {
		out.writes, said = writesNo, true
	}
	return out, said
}

func targetsClaim(expected string, out *claim) bool {
	said := false
	if strings.Contains(expected, "отправка (push)") {
		out.writes, out.network, said = writesYes, true, true
	}
	if expected == "не отправка" {
		out.noNetwork, said = true, true
	}
	if cut := strings.Index(expected, "цель записи — "); cut >= 0 {
		target := expected[cut+len("цель записи — "):]
		if end := strings.Index(target, ","); end >= 0 {
			out.once = strings.Contains(target[end:], "один раз")
			target = target[:end]
		}
		out.target, out.writes, said = target, writesYes, true
	}
	if strings.Contains(expected, "целей записи нет") {
		out.noTargets, said = true, true
	}
	return said
}

func loadCases(t *testing.T) []corpusCase {
	t.Helper()
	raw, err := os.ReadFile(casesPath)
	if err != nil {
		t.Fatalf("корпус не прочитан: %v", err)
	}
	var cases []corpusCase
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatalf("корпус не разобран: %v", err)
	}
	return cases
}

func TestCorpus(t *testing.T) {
	rules := testRules(t)
	cases := loadCases(t)
	if len(cases) == 0 {
		t.Fatal("корпус пуст")
	}
	for _, one := range cases {
		key := one.Command + "|" + one.Expected
		t.Run(key, func(t *testing.T) {
			if reason, ok := skipped[key]; ok {
				t.Skip(reason)
			}
			want, overridden := overrides[key]
			if !overridden {
				want = claimOf(one.Expected)
			}
			if want.empty() {
				t.Fatalf("ожидание «%s» не переведено в утверждение: добавь семью фраз или строку в overrides", one.Expected)
			}
			got := verdictOf(one.Command, "", rules)
			check(t, want, got, one)
		})
	}
}

func check(t *testing.T, want claim, got Verdict, one corpusCase) {
	t.Helper()
	if want.writes != "" && got.Writes != want.writes {
		t.Errorf("writes = %q, ждали %q\n  команда: %s\n  ожидание: %s\n  причина вердикта: %s",
			got.Writes, want.writes, one.Command, one.Expected, got.Reason)
	}
	if want.notWrites != "" && got.Writes == want.notWrites {
		t.Errorf("writes = %q, а такого быть не должно\n  команда: %s\n  ожидание: %s\n  причина вердикта: %s",
			got.Writes, one.Command, one.Expected, got.Reason)
	}
	if want.target != "" {
		found := 0
		for _, target := range got.Targets {
			if target.Path == want.target {
				found++
			}
		}
		if found == 0 {
			t.Errorf("цели «%s» нет в списке: %v\n  команда: %s", want.target, got.Targets, one.Command)
		}
		if want.once && found != 1 {
			t.Errorf("цель «%s» названа %d раз(а), ждали один\n  команда: %s", want.target, found, one.Command)
		}
	}
	if want.noTargets && len(got.Targets) > 0 {
		t.Errorf("целей быть не должно, а есть: %v\n  команда: %s", got.Targets, one.Command)
	}
	if want.network && !hasKind(got.Targets, kindNetwork) {
		t.Errorf("ждали цель вида «сеть», а список: %v\n  команда: %s", got.Targets, one.Command)
	}
	if want.noNetwork && hasKind(got.Targets, kindNetwork) {
		t.Errorf("цели вида «сеть» быть не должно, а список: %v\n  команда: %s", got.Targets, one.Command)
	}
	if want.blames != "" && !strings.Contains(got.Reason, want.blames) {
		t.Errorf("причина «%s» не называет виновника «%s»\n  команда: %s", got.Reason, want.blames, one.Command)
	}
}

func hasKind(targets []Target, kind string) bool {
	for _, target := range targets {
		if target.Kind == kind {
			return true
		}
	}
	return false
}

// verdictOf — тот же путь, которым ходит команда `shell-tree verdict`.
func verdictOf(command, cwd string, rules *Rules) Verdict {
	tree, err := Parse(command, cwd)
	if err != nil {
		return Unjudged(err)
	}
	return Judge(tree, rules)
}

func testRules(t *testing.T) *Rules {
	t.Helper()
	rules, err := LoadRules("../data/shell/read-only-rules.json")
	if err != nil {
		t.Fatalf("списки не прочитаны: %v", err)
	}
	return rules
}

// Счётчик кейсов держит корпус на виду: упал он или разросся — видно сразу.
func TestCorpusSize(t *testing.T) {
	if got := len(loadCases(t)); got != 233 {
		t.Errorf("кейсов %d, а было 233: корпус изменился — пересмотри пропуски", got)
	}
}

func TestSkipsAreRealCases(t *testing.T) {
	cases := loadCases(t)
	known := map[string]bool{}
	for _, one := range cases {
		known[one.Command+"|"+one.Expected] = true
	}
	for key := range skipped {
		if !known[key] {
			t.Errorf("пропуск «%s» не про кейс корпуса", key)
		}
	}
	for key := range overrides {
		if !known[key] {
			t.Errorf("override «%s» не про кейс корпуса", key)
		}
	}
	fmt.Fprintf(os.Stderr, "корпус: %d кейсов, пропущено %d\n", len(cases), len(skipped))
}
