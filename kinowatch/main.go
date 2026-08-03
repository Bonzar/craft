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
		enrich        = flag.Bool("enrich", false, "собрать реестр, добыть адреса и координаты, напечатать наблюдения")
		eaisBase      = flag.String("eais-base", "https://ekinobilet.fond-kino.ru", "база ЕАИС")
		region        = flag.String("region", moscowRegionCode, "код региона в листинге ЕАИС")
		timeoutSec    = flag.Int("timeout", 60, "таймаут одного HTTP-запроса, сек")
		retries       = flag.Int("retries", 3, "ретраи на сетевых ошибках и 5xx")
		limit         = flag.Int("limit", 0, "геокодировать только первые N площадок (0 — все)")
		probe         = flag.Bool("probe", false, "опросить площадки реестра (stdin) по фильму и напечатать статусы")
		probeFilm     = flag.String("film", "", "название искомого фильма")
		probeProfile  = flag.String("film-profile", "", "файл с профилем фильма: обёртки, хронометраж, синопсис")
		probeDays     = flag.Int("days", 7, "горизонт опроса в днях от сегодня")
		coverageMode  = flag.Bool("coverage", false, "посчитать покрытие по реестру (stdin) и упасть, пока оно неполно")
		coverageShort = flag.Bool("short", false, "для --coverage: одна строка вместо списка")

		probeGeo  = flag.String("probe-geo", "", "прогнать каскад по одному названию и показать решение по шагам")
		probeAddr = flag.String("probe-address", "", "адрес для --probe-geo, если он известен")
		probeNet  = flag.Bool("probe-network", false, "считать площадку из --probe-geo сетевой")

		// Туннель поднимает рутина, бинарник получает готовый адрес: держать
		// внутри запуск xray значило бы смешать две разные ответственности и
		// сделать прогон непроверяемым без сети.
		proxyAddr    = flag.String("proxy", "", "socks5-адрес российского выхода, напр. 127.0.0.1:10808")
		proxyCountry = flag.String("proxy-country", "RU", "какую страну обязан показать выход туннеля")
		checkProxy   = flag.Bool("check-proxy", false, "проверить адрес и страну выхода через --proxy и выйти")
	)
	flag.Parse()

	client := newClient(*timeoutSec, *retries)

	switch {
	case *buildRegistry:
		runBuildRegistry(client, *eaisBase, *region)
	case *enrich:
		runEnrich(client, newGeoClient(*timeoutSec, *retries), *eaisBase, *region, *limit)
	case *probe:
		runProbe(client, *probeFilm, *probeProfile, *probeDays)
	case *coverageMode:
		runCoverage(*coverageShort)
	case *probeGeo != "":
		runProbeGeo(newGeoClient(*timeoutSec, *retries), *probeGeo, *probeAddr, *probeNet)
	case *checkProxy:
		runCheckProxy(*proxyAddr, *proxyCountry, *timeoutSec, *retries)
	default:
		fail("режим не выбран: укажи --build-registry, --enrich или --probe")
	}
}

// runCheckProxy печатает, куда на самом деле выходит туннель.
//
// Отдельный режим нужен потому, что проверка выхода легко врёт: поднявшийся
// процесс xray ничего не доказывает, а запрос мимо туннеля показывает адрес
// самого контейнера. Дешевле убедиться заранее, чем объяснять потом, почему
// целая сеть оказалась «недоступна».
func runCheckProxy(addr, wantCountry string, timeoutSec, retries int) {
	if addr == "" {
		fail("--check-proxy требует --proxy")
	}
	c, err := newTunnelClient(addr, timeoutSec, retries)
	if err != nil {
		fail("%v", err)
	}

	res := checkTunnel(c, wantCountry)
	out, err := json.MarshalIndent(res, "", "  ")
	if err != nil {
		fail("сериализация: %v", err)
	}
	fmt.Println(string(out))
	if !res.OK {
		fail("выход не подтверждён как %s: %s", wantCountry, res.Reason)
	}
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

// EnrichReport — то, что отдаёт --enrich: наблюдения для upsert плюс числа,
// которыми меряется сам геокодер.
//
// Числа здесь не украшение отчёта: ожидаемой доли успеха у каскада нет ни для
// одного пути (измерение 12/12 сделано на смешанной выборке, где были и сетевые
// площадки), поэтому первое измерение — этот прогон.
type EnrichReport struct {
	FetchedAt string `json:"fetchedAt"`
	// ListingTotal — размер московского листинга ДО обрезки по МКАД. В реестре
	// его нет: туда ложатся только площадки в охвате, а вердикт достоверности
	// сверяется с вилкой, построенной по всей Москве.
	ListingTotal   int                 `json:"listingTotal"`
	InScope        int                 `json:"inScope"`
	Enriched       map[string]int      `json:"enriched"`
	Paths          map[string]PathStat `json:"paths"`
	Unverified     int                 `json:"unverified"`
	NameDuplicates int                 `json:"nameDuplicates"`
	// Binding — покрытие каналами по сетям. Отдельно от Paths: там речь про
	// координаты площадки, здесь — про то, есть ли к ней вообще запрос.
	Binding     []NetworkBinding `json:"binding"`
	BoundVenues int              `json:"boundVenues"`
	// FixedChannels — сколько каналов проставлено поштучно, вне справочников.
	FixedChannels int                 `json:"fixedChannels"`
	Observations  []CinemaObservation `json:"observations"`
	Errors        []string            `json:"errors,omitempty"`
	// GeoErrors отдельно от Errors: отказ геокодера по одной площадке прогон не
	// рушит, но и тонуть в общем списке не должен — по нему видно, чего стоит
	// доверять числам в Paths.
	GeoErrors []string `json:"geoErrors,omitempty"`
}

// PathStat — сколько площадок пошло этим путём и скольким он дал координаты.
type PathStat struct {
	Attempted int `json:"attempted"`
	Solved    int `json:"solved"`
}

// Имена путей каскада. Совпадают с разложением популяции в плане, потому что
// именно по ним меряется доля успеха.
const (
	pathAddressSolo    = "address-solo"    // адрес есть, одиночка: все четыре ступени
	pathAddressNetwork = "address-network" // адрес есть, сетевая: без поиска по названию
	pathTitleOnly      = "title-only"      // адреса нет, одиночка: только поиск по названию
	pathSkippedNetwork = "skipped-network" // адреса нет, сетевая: ни одной ступени
	pathFromEnricher   = "from-enricher"   // координаты пришли готовыми, запросов не было
	pathNameDuplicate  = "name-duplicate"  // имя неразличимо, искать по нему нельзя
)

func pathOf(hasAddress, isNetwork bool) string {
	switch {
	case hasAddress && !isNetwork:
		return pathAddressSolo
	case hasAddress && isNetwork:
		return pathAddressNetwork
	case !hasAddress && !isNetwork:
		return pathTitleOnly
	default:
		return pathSkippedNetwork
	}
}

func runEnrich(c, geo *Client, base, region string, limit int) {
	rows, _, errs := fetchAllEaisPages(c, base, region)
	decisions := applyCityScope(rows)
	now := nowRFC3339()

	report := EnrichReport{
		FetchedAt:    now,
		ListingTotal: len(rows),
		Enriched:     map[string]int{},
		Paths:        map[string]PathStat{},
		Errors:       errs,
	}

	// Обогатители необязательны: отказ любого из них не рушит прогон, площадки
	// просто уходят на ступень поиска по названию.
	jsonClient := newJSONClient(60, 3)
	var venues []EnrichedVenue
	if karo, err := fetchKaro(jsonClient, karoDirectoryURL); err != nil {
		report.Errors = append(report.Errors, fmt.Sprintf("КАРО: %v", err))
	} else {
		venues = append(venues, karo...)
		report.Enriched["karo"] = len(karo)
	}
	if osm, err := fetchOverpass(jsonClient, overpassURL); err != nil {
		report.Errors = append(report.Errors, fmt.Sprintf("Overpass: %v", err))
	} else {
		venues = append(venues, osm...)
		report.Enriched["osm"] = len(osm)
	}

	inScopeRows := make([]EaisRow, 0, len(decisions))
	for _, d := range decisions {
		if d.InScope {
			inScopeRows = append(inScopeRows, d.Row)
		}
	}
	matched, ambiguous := matchEnrichers(inScopeRows, venues)
	report.NameDuplicates = len(ambiguous)
	ambiguousSet := map[string]bool{}
	for _, id := range ambiguous {
		ambiguousSet[id] = true
	}

	obs := buildCinemaObservations(decisions, now)
	report.InScope = len(obs)

	// Привязка идёт до геокодирования: она решает, ЕСТЬ ли у площадки запрос за
	// расписанием, и от координат никак не зависит.
	report.Binding = bindAllNetworks(jsonClient.getText, obs)

	// Каналы, найденные поштучно: у одиночек справочника нет по определению.
	fixed, orphans := applyFixedChannels(obs)
	report.FixedChannels = fixed
	for _, o := range orphans {
		report.Errors = append(report.Errors, "запись канала без строки реестра: "+o)
	}

	for i := range obs {
		if obs[i].Fields[fSourceKind] != "" {
			report.BoundVenues++
		}
	}

	geocoded := 0
	for i := range obs {
		o := &obs[i]

		if v, ok := matched[o.Key]; ok {
			setEnriched(o, v, now)
		}
		isNetwork := strings.TrimSpace(o.Fields[fNetwork]) != ""

		if hasCoords(*o) {
			// Координаты пришли готовыми от обогатителя — геокодер не нужен.
			bump(report.Paths, pathFromEnricher, true)
			continue
		}

		if ambiguousSet[o.Key] {
			// Две строки листинга с одним именем — искать их по имени нельзя:
			// запрос у них одинаковый, и обе получат одну точку. Первый живой
			// прогон именно так и выдал двум «PRIME CINEMA» общие координаты.
			addNote(o.Fields, noteGeoNameDup)
			applyGeo(o, GeoOutcome{}, now)
			bump(report.Paths, pathNameDuplicate, false)
			continue
		}
		if limit > 0 && geocoded >= limit {
			continue
		}

		target := GeoTarget{
			Name:      o.Name,
			Address:   o.Fields[fAddress],
			IsNetwork: isNetwork,
		}
		path := pathOf(strings.TrimSpace(target.Address) != "", isNetwork)

		var outcome GeoOutcome
		if path != pathSkippedNetwork {
			outcome = geocode(geo, target, photonBase, nominatimBase)
			geocoded++
		}
		applyGeo(o, outcome, now)

		bump(report.Paths, path, outcome.Point != nil)
		for _, n := range outcome.Notes {
			if n == noteGeoUnverified {
				report.Unverified++
			}
		}
		// Отказ геокодера — не «площадки не существует», и в отчёте эти вещи
		// обязаны различаться, иначе молчащий сервис выглядит как пустой город.
		for _, e := range outcome.Errors {
			report.GeoErrors = append(report.GeoErrors, o.Name+" — "+e)
		}
	}

	report.Observations = obs

	out, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		fail("сериализация результата: %v", err)
	}
	fmt.Println(string(out))
}

// runProbeGeo прогоняет каскад по одной площадке и печатает решение по шагам.
//
// Нужен потому, что «координат нет» может значить четыре разных вещи: ступень
// не исполнялась, геокодер отказал, ответ не прошёл гейт, кросс-чек развёл
// точки. По сводному отчёту они неотличимы, а чинить их надо по-разному.
func runProbeGeo(geo *Client, name, address string, isNetwork bool) {
	t := GeoTarget{Name: name, Address: address, IsNetwork: isNetwork}
	steps := planSteps(strings.TrimSpace(address) != "", isNetwork)

	fmt.Printf("площадка: %s\nсетевая: %v\nступени: %v\n\n", name, isNetwork, steps)
	for _, s := range steps {
		q := stepQuery(s, t)
		if q == "" {
			fmt.Printf("%-16s пропуск: нечего подать на вход\n", s)
			continue
		}
		fmt.Printf("%-16s запрос %q\n", s, q)

		// Показываем сырое решение каждой ступени: что ответил геокодер, что
		// сказал гейт и что показал кросс-чек. Иначе «координат нет» — чёрный
		// ящик с четырьмя разными причинами внутри.
		switch s {
		case stepNominatimNorm:
			pt, _, err := queryNominatim(geo, nominatimBase, q)
			fmt.Printf("%-16s   nominatim: %s\n", "", describePoint(pt, err))
		default:
			want := ""
			if s == stepPhotonTitle {
				want = name
			}
			pt, _, err := queryPhoton(geo, photonBase, q, want)
			fmt.Printf("%-16s   photon: %s\n", "", describePoint(pt, err))
			if needsCrossCheck(s) {
				check, _, cerr := queryNominatim(geo, nominatimBase, q)
				fmt.Printf("%-16s   кросс-чек: %s\n", "", describePoint(check, cerr))
				if pt != nil && check != nil {
					fmt.Printf("%-16s   расхождение: %.2f км (порог %.1f)\n",
						"", haversineKm(pt.Lat, pt.Lon, check.Lat, check.Lon), crossCheckLimitKm)
				}
			}
		}
	}

	out := geocode(geo, t, photonBase, nominatimBase)
	fmt.Println()
	if out.Point != nil {
		fmt.Printf("итог: %.6f, %.6f (ступень %s)\nадрес: %s\n",
			out.Point.Lat, out.Point.Lon, out.Point.Step, out.Point.Address)
	} else {
		fmt.Println("итог: координат нет")
	}
	if len(out.Notes) > 0 {
		fmt.Printf("пометки: %v\n", out.Notes)
	}
	if len(out.Errors) > 0 {
		fmt.Printf("ошибки: %v\n", out.Errors)
	}
	fmt.Printf("последний запрос: %s\n", out.Evidence)
}

func describePoint(p *GeoPoint, err error) string {
	if err != nil {
		return "ошибка: " + err.Error()
	}
	if p == nil {
		return "ничего не прошло гейт"
	}
	return fmt.Sprintf("%.6f, %.6f (%s)", p.Lat, p.Lon, p.Address)
}

func bump(stats map[string]PathStat, path string, solved bool) {
	s := stats[path]
	s.Attempted++
	if solved {
		s.Solved++
	}
	stats[path] = s
}

// fail — единственный способ завершиться с ошибкой уровня CLI.
// Ошибки уровня страницы не фатальны: они копятся в Errors результата, потому
// что половина собранного реестра полезнее, чем пустой выход.
func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "kinowatch: "+format+"\n", args...)
	os.Exit(1)
}
