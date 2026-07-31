package main

// kinowatch — обходчик кинотеатров Москвы: проверяет каждую площадку на наличие
// билетов на заданный фильм, ходя в СОБСТВЕННЫЙ канал продаж каждой из них.
//
// Отличие от агрегатора и причина существования: агрегаторы неполны (в них
// только площадки с договорённостями), имеют рекламные приоритеты и отстают от
// касс. Здесь каждый кинотеатр опрашивается отдельным запросом, а сторонние
// витрины идут вторым слоем — как перекрёстная проверка, а не как источник.
//
// Про Craft этот бинарник не знает ничего: реестр приходит на stdin, результат
// уходит в stdout. Так сделано потому, что новые документы сами в scope
// connect-ссылки не попадают, и чтение реестра напрямую упёрлось бы в 403.
// Запись в коллекции делает рутинная сессия MCP-командами.
//
// Устройство файлов — отступление от craft-sync, который живёт одним main.go на
// 1140 строк. Здесь логики заметно больше (реестр, адаптеры, матчинг, лестница
// ступеней), и один файл перестал бы читаться. Настоящие инварианты сохранены:
// package main, ноль внешних зависимостей, плоская директория, без подпакетов.

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
)

// Режим выбирается непустым флагом, как в craft-sync: подкоманд нет,
// диспетчеризация — одной проверкой в main().
func main() {
	var (
		buildRegistry = flag.Bool("build-registry", false, "собрать скелет реестра из ЕАИС и напечатать JSON")
		eaisBase      = flag.String("eais-base", "https://ekinobilet.fond-kino.ru", "база ЕАИС")
		region        = flag.String("region", moscowRegionCode, "код региона в листинге ЕАИС")
		timeoutSec    = flag.Int("timeout", 60, "таймаут одного HTTP-запроса, сек")
		retries       = flag.Int("retries", 3, "ретраи на сетевых ошибках и 5xx")
	)
	flag.Parse()

	client := newClient(*timeoutSec, *retries)

	if *buildRegistry {
		runBuildRegistry(client, *eaisBase, *region)
		return
	}

	fail("режим не выбран: укажи --build-registry (остальные режимы появятся на следующих этапах)")
}

// RegistrySkeleton — то, что отдаёт --build-registry.
// Это ещё не реестр: здесь нет ни адресов, ни сайтов, ни классов. Только
// перечень из ЕАИС, решение по городу и подсказки о дублях.
type RegistrySkeleton struct {
	Source        string          `json:"source"`
	FetchedAt     string          `json:"fetchedAt"`
	Pages         int             `json:"pages"`
	TotalRows     int             `json:"totalRows"`
	InScope       int             `json:"inScope"`
	Networked     int             `json:"networked"`
	Standalone    int             `json:"standalone"`
	Rows          []ScopeDecision `json:"rows"`
	DuplicateHint []DuplicateHint `json:"duplicateHints,omitempty"`
	Errors        []string        `json:"errors,omitempty"`
}

func runBuildRegistry(c *Client, base, region string) {
	rows, pages, errs := fetchAllEaisPages(c, base, region)
	decisions := applyCityScope(rows)

	skel := RegistrySkeleton{
		Source:        "eais",
		FetchedAt:     nowRFC3339(),
		Pages:         pages,
		TotalRows:     len(rows),
		Rows:          decisions,
		DuplicateHint: findDuplicateHints(rows),
		Errors:        errs,
	}
	for _, d := range decisions {
		if d.InScope {
			skel.InScope++
		}
		if strings.TrimSpace(d.Row.Network) == "" {
			skel.Standalone++
		} else {
			skel.Networked++
		}
	}

	out, err := json.MarshalIndent(skel, "", "  ")
	if err != nil {
		fail("сериализация результата: %v", err)
	}
	fmt.Println(string(out))
}

// fail — единственный способ завершиться с ошибкой уровня CLI.
// Ошибки уровня страницы не фатальны: они копятся в Errors результата, потому
// что половина собранного реестра полезнее, чем пустой выход.
func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "kinowatch: "+format+"\n", args...)
	os.Exit(1)
}
