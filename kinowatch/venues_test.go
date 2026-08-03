package main

// Тесты привязки площадок к каналам их сетей.
//
// Проверяется главное свойство: у КАЖДОЙ строки реестра есть исход. Молчаливо
// пропущенная строка — это площадка, которую прогон никогда не опросит, и
// заметить это по отчёту будет нельзя.

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"testing"
)

// karoRows — московские площадки КАРО так, как они записаны в реестре ЕАИС.
// Названия дословные: именно на них ломается наивное сравнение по полному
// имени, потому что сеть в справочнике зовёт их иначе («КАРО 7 Атриум» против
// «7 Атриум»).
var karoRows = []EaisRow{
	{ID: "1578", City: "Москва г", Company: "КАРО 7 Атриум", Network: "КАРО ФИЛЬМ"},
	{ID: "1584", City: "Москва г", Company: "КАРО 11 Октябрь", Network: "КАРО ФИЛЬМ"},
	{ID: "1608", City: "Москва г", Company: "КАРО 6 Щука", Network: "КАРО ФИЛЬМ"},
	{ID: "5896", City: "Москва г", Company: "КАРО Sky 17 Авиапарк", Network: "КАРО ФИЛЬМ"},
	{ID: "9095", City: "Москва г", Company: "КАРО 6 Будапешт", Network: "КАРО ФИЛЬМ"},
	{ID: "9096", City: "Москва г", Company: "КАРО 4 Высота", Network: "КАРО ФИЛЬМ"},
	{ID: "9100", City: "Москва г", Company: "КАРО 10 София", Network: "КАРО ФИЛЬМ"},
	{ID: "10335", City: "Москва г", Company: "КАРО 6 Киргизия", Network: "КАРО ФИЛЬМ"},
	{ID: "10389", City: "Москва г", Company: "КАРО 8 Саларис", Network: "КАРО ФИЛЬМ"},
	{ID: "7626", City: "Москва г", Company: "КАРО Фили", Network: "КАРО ФИЛЬМ"},
	{ID: "7625", City: "Москва г", Company: "КАРО ВДНХ", Network: "КАРО ФИЛЬМ"},
	{ID: "7178", City: "Москва г", Company: "КАРО Музеон", Network: "КАРО ФИЛЬМ"},
	{ID: "7151", City: "Москва г", Company: "КАРО Эрмитаж", Network: "КАРО ФИЛЬМ"},
	{ID: "8860", City: "Москва г", Company: "КАРО Музей Москвы", Network: "КАРО ФИЛЬМ"},
	{ID: "8656", City: "Москва г", Company: "КАРО под звёздами: Черемушки", Network: "КАРО ФИЛЬМ"},
	{ID: "8666", City: "Москва г", Company: "КАРО парк Садовники", Network: "КАРО ФИЛЬМ"},
}

func TestParseKaroVenues(t *testing.T) {
	vs, err := parseKaroVenues(readFixture(t, "karo-directory.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(vs) < 3 {
		t.Fatalf("площадок %d, в фикстуре их больше", len(vs))
	}
	for _, v := range vs {
		if v.ID == "" || v.Name == "" {
			t.Errorf("площадка без идентификатора или названия: %+v", v)
		}
		if v.Kind != kindKaro {
			t.Errorf("тип канала %q, ожидался %q", v.Kind, kindKaro)
		}
	}
}

func TestParseKinomaxVenues(t *testing.T) {
	vs, err := parseKinomaxVenues(readFixture(t, "kinomax-cinemas.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(vs) < 3 {
		t.Fatalf("площадок %d, в фикстуре их больше", len(vs))
	}
	for _, v := range vs {
		// В запрос расписания идёт ident («vodny»), а не числовой id: подстановка
		// числа дала бы 404 на каждой площадке сети.
		if _, err := strconv.Atoi(v.ID); err == nil {
			t.Errorf("в ID попало число %q — Киномакс ждёт ident вроде «vodny»", v.ID)
		}
		if v.Name == "" {
			t.Errorf("площадка без названия: %+v", v)
		}
	}
}

func TestParseMoskinoVenues(t *testing.T) {
	vs, err := parseMoskinoVenues(readFixture(t, "moskino-venues.html"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(vs) < 3 {
		t.Fatalf("слагов %d, в фикстуре их больше", len(vs))
	}
	seen := map[string]bool{}
	for _, v := range vs {
		if seen[v.ID] {
			t.Errorf("слаг %q встретился дважды", v.ID)
		}
		seen[v.ID] = true
	}

	// Сменившаяся вёрстка — ошибка, а не пустой список площадок.
	if _, err := parseMoskinoVenues("<html><body>нет ссылок</body></html>"); err == nil {
		t.Error("разбор промолчал об отсутствии ссылок")
	}
}

// Главный кейс: справочник сети и реестр расходятся в обе стороны, и у каждой
// строки обязан быть исход.
func TestBindKaroVenues(t *testing.T) {
	vs, err := parseKaroVenues(readFixture(t, "karo-directory.json"))
	if err != nil {
		t.Fatalf("разбор справочника: %v", err)
	}

	obs := buildCinemaObservations(applyCityScope(karoRows), "2026-08-03T10:00:00Z")
	res := bindNetworkVenues(obs, vs)

	if res.Bound != 9 {
		t.Errorf("привязано %d строк, ожидалось 9", res.Bound)
	}
	if len(res.Unmatched) != 7 {
		t.Errorf("без канала %d строк, ожидалось 7: %v", len(res.Unmatched), res.Unmatched)
	}
	// Иридиум (Зеленоград) и Реутов (область) есть в справочнике, но не в
	// московском реестре. Терять их молча нельзя — это либо другая территория,
	// либо пропуск в самом реестре.
	if len(res.Orphans) != 2 {
		t.Errorf("площадок справочника без пары %d, ожидалось 2: %v", len(res.Orphans), res.Orphans)
	}

	// У каждой строки исход: либо канал, либо непокрытость с причиной.
	for _, o := range obs {
		params := o.Fields[fSourceParams]
		class := o.Fields[fStatusClass]
		if params == "" && o.Fields[fLastError] == "" {
			t.Errorf("%q осталась без исхода: params=%q class=%q", o.Name, params, class)
		}
		if params != "" && !strings.HasPrefix(params, "venue=") {
			t.Errorf("%q: параметры канала записаны как %q", o.Name, params)
		}
	}

	// Площадка без канала остаётся В ЗНАМЕНАТЕЛЕ: иначе семь работающих
	// кинотеатров молча перестали бы считаться недоработкой.
	for _, name := range res.Unmatched {
		for _, o := range obs {
			if o.Name != name {
				continue
			}
			if o.Fields[fStatusClass] == classNoSource {
				t.Errorf("%q объявлена непокрываемой, хотя домен сети жив", name)
			}
			if !keepsInDenominator(o.Fields[fStatusClass]) {
				t.Errorf("%q выпала из знаменателя покрытия", name)
			}
			if o.Fields[fLastError] == "" {
				t.Errorf("%q без причины — не отличить от «адаптер не написан»", name)
			}
		}
	}
}

// Привязанная площадка перестаёт быть непокрытой: канал у неё теперь есть.
func TestBindClearsUncovered(t *testing.T) {
	vs, err := parseKaroVenues(readFixture(t, "karo-directory.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	obs := buildCinemaObservations(applyCityScope(karoRows[:1]), "2026-08-03T10:00:00Z")
	if obs[0].Fields[fStatusClass] != classUncovered {
		t.Fatalf("до привязки класс %q, ожидался %q", obs[0].Fields[fStatusClass], classUncovered)
	}

	bindNetworkVenues(obs, vs)

	if obs[0].Fields[fStatusClass] == classUncovered {
		t.Error("после привязки площадка всё ещё числится непокрытой")
	}
	if obs[0].Fields[fSourceKind] != kindKaro {
		t.Errorf("тип канала не проставлен: %q", obs[0].Fields[fSourceKind])
	}
}

// Клоны и площадки без сеансов привязка не трогает: у первых канал ведущей
// записи, у вторых сеансов нет вовсе.
func TestBindSkipsClonesAndVenuesWithoutScreenings(t *testing.T) {
	rows := []EaisRow{
		{ID: "1", City: "Москва г", Company: "Киномакс Водный", Network: "Созвездие"},
		{ID: "2", City: "Москва г", Company: "Коперто"},
	}
	obs := buildCinemaObservations(applyCityScope(rows), "2026-08-03T10:00:00Z")
	res := bindNetworkVenues(obs, []NetworkVenue{{ID: "vodny", Name: "Киномакс-Водный Москва", Kind: kindKinomax}})

	if res.Bound != 0 {
		t.Errorf("привязано %d строк, ожидалось 0", res.Bound)
	}
	for _, o := range obs {
		// У клона в тех же параметрах законно стоит ссылка на ведущую запись —
		// проверяется отсутствие именно канала опроса.
		if strings.HasPrefix(o.Fields[fSourceParams], "venue=") {
			t.Errorf("%q получила канал, хотя привязке не подлежит: %q", o.Name, o.Fields[fSourceParams])
		}
	}
	if obs[0].Fields[fStatusClass] != classCloneOf {
		t.Errorf("клон потерял свой класс: %q", obs[0].Fields[fStatusClass])
	}
	if obs[1].Fields[fStatusClass] != classNoOnlineSale {
		t.Errorf("площадка без сеансов потеряла свой класс: %q", obs[1].Fields[fStatusClass])
	}
}

// Две строки с неразличимым названием не привязываются ни одна: иначе обе
// получили бы один канал и одно расписание.
func TestBindRefusesAmbiguousNames(t *testing.T) {
	rows := []EaisRow{
		{ID: "1", City: "Москва г", Company: "КАРО 7 Атриум", Network: "КАРО ФИЛЬМ"},
		{ID: "2", City: "Москва г", Company: "КАРО 9 Атриум", Network: "КАРО ФИЛЬМ"},
	}
	obs := buildCinemaObservations(applyCityScope(rows), "2026-08-03T10:00:00Z")
	res := bindNetworkVenues(obs, []NetworkVenue{{ID: "3", Name: "7 Атриум", Kind: kindKaro}})

	if res.Bound != 0 {
		t.Errorf("привязано %d строк при неразличимых названиях, ожидалось 0", res.Bound)
	}
	if len(res.Ambiguous) != 2 {
		t.Errorf("неразличимых отмечено %d, ожидалось 2: %v", len(res.Ambiguous), res.Ambiguous)
	}
	for _, o := range obs {
		if o.Fields[fSourceParams] != "" {
			t.Errorf("%q привязана вопреки неразличимости", o.Name)
		}
	}
}

// venueKey снимает имя сети и ведущий номер зала — без этого не совпала бы ни
// одна площадка.
func TestVenueKey(t *testing.T) {
	cases := []struct{ registry, directory string }{
		{"КАРО 7 Атриум", "7 Атриум"},
		{"КАРО 11 Октябрь", "11 Октябрь"},
		{"КАРО Sky 17 Авиапарк", "Sky 17 Авиапарк"},
		{"КАРО под звёздами: Черемушки", "Черемушки"},
		{"Киномакс-Водный", "Киномакс-Водный Москва"},
		// Синема-Стар ставит город не в хвост, а сразу после имени сети.
		{"Синема Стар Москва Европарк", "Синема Стар Европарк"},
		{"Синема Стар Москва Марьина Роща", "Синема Стар Марьина роща"},
		{"Синема Стар Москва Принц Плаза", "Синема Стар Принц Плаза"},
	}
	for _, c := range cases {
		if got, want := venueKey(c.registry), venueKey(c.directory); got != want {
			t.Errorf("ключи разошлись: %q → %q, %q → %q", c.registry, got, c.directory, want)
		}
	}
}

// Город снимается только вместе с именем сети.
//
// У Миража «Москва» — само название кинотеатра, а не приписка о городе:
// безусловное снятие превратило бы три разные площадки в «мари», «отрадное» и
// «ростокино», то есть тихо переименовало бы их.
func TestVenueKeyKeepsCityWhenItIsTheName(t *testing.T) {
	cases := map[string]string{
		"Москва МАРИ":      "москва мари",
		"Москва ОТРАДНОЕ":  "москва отрадное",
		"Москва РОСТОКИНО": "москва ростокино",
		// Имя площадки — сам город: снимать нечего, иначе ключ обнулится.
		"Москва": "москва",
	}
	for name, want := range cases {
		if got := venueKey(name); got != want {
			t.Errorf("venueKey(%q) = %q, ожидалось %q", name, got, want)
		}
	}
}

// registryRows читает строки реестра из фикстуры: привязка обязана проверяться
// на настоящих названиях, а не на придуманных. Именно живые названия ломают
// наивное сравнение — юридическая обёртка у Москино, кавычки и «г. Москва» у
// Киномакса.
func registryRows(t *testing.T, network func(string) bool) []EaisRow {
	t.Helper()
	var reg []struct {
		City    string `json:"city"`
		EaisID  string `json:"eaisId"`
		Company string `json:"company"`
		Network string `json:"network"`
	}
	if err := json.Unmarshal([]byte(readFixture(t, "registry-networks.json")), &reg); err != nil {
		t.Fatalf("разбор фикстуры реестра: %v", err)
	}
	var rows []EaisRow
	for _, r := range reg {
		if network(r.Network) {
			rows = append(rows, EaisRow{ID: r.EaisID, City: r.City, Company: r.Company, Network: r.Network})
		}
	}
	if len(rows) == 0 {
		t.Fatal("в фикстуре нет строк выбранной сети")
	}
	return rows
}

// Москино: 22 строки реестра против 21 площадки на сайте сети.
func TestBindMoskino(t *testing.T) {
	vs, err := parseMoskinoVenues(readFixture(t, "moskino-venues.html"))
	if err != nil {
		t.Fatalf("разбор справочника: %v", err)
	}
	rows := registryRows(t, func(n string) bool { return n == "Москино" })

	obs := buildCinemaObservations(applyCityScope(rows), "2026-08-03T10:00:00Z")
	res := bindNetworkVenues(obs, vs)

	if res.Bound != 21 {
		t.Errorf("привязано %d из %d, ожидался 21", res.Bound, len(rows))
	}
	// «Москино Звезда» есть в реестре, но не на сайте сети — честная
	// непокрытость, а не повод объявить площадку несуществующей.
	if len(res.Unmatched) != 1 {
		t.Errorf("без канала %d строк, ожидалась 1: %v", len(res.Unmatched), res.Unmatched)
	}
	if len(res.Orphans) != 0 {
		t.Errorf("площадки сайта без пары: %v", res.Orphans)
	}

	for _, o := range obs {
		if o.Fields[fSourceParams] == "" && o.Fields[fLastError] == "" {
			t.Errorf("%q осталась без исхода", o.Name)
		}
	}
}

// Строка ищет себя только в справочнике своей сети.
//
// «Москино Музеон» и «КАРО Музеон» — реальная пара листинга: разные площадки
// разных сетей с одинаковым ключом «музеон». Без разделения по сетям обе
// привязывались бы к единственному кандидату из справочника Москино, и КАРО
// получил бы чужое расписание с видом полной достоверности.
func TestBindScopesRowsToTheirOwnNetwork(t *testing.T) {
	rows := []EaisRow{
		{ID: "1", City: "Москва г", Company: "«Москино Музеон»", Network: "Москино"},
		{ID: "2", City: "Москва г", Company: "КАРО Музеон", Network: "КАРО ФИЛЬМ"},
	}
	obs := buildCinemaObservations(applyCityScope(rows), "2026-08-03T10:00:00Z")
	res := bindNetworkVenues(obs, []NetworkVenue{{ID: "moskino_muzeon", Name: "Музеон", Kind: kindMoskino}})

	if res.Bound != 1 {
		t.Fatalf("привязано %d, ожидалась одна строка Москино: %+v", res.Bound, res)
	}
	if got := obs[0].Fields[fSourceParams]; got != "venue=moskino_muzeon" {
		t.Errorf("«Москино Музеон» не получила свой канал: %q", got)
	}
	if got := obs[1].Fields[fSourceParams]; got != "" {
		t.Errorf("«КАРО Музеон» привязана к чужой сети: %q", got)
	}
	// Строка чужой сети не получает и вердикта «нет в справочнике своей сети»:
	// справочника КАРО в этом вызове не было вовсе.
	if got := obs[1].Fields[fLastError]; got != "" {
		t.Errorf("«КАРО Музеон» получила вердикт по чужому справочнику: %q", got)
	}
}

// Киномакс: у каждой площадки в канал идёт ident, а клоны сети привязку не
// получают вовсе.
func TestBindKinomax(t *testing.T) {
	vs, err := parseKinomaxVenues(readFixture(t, "kinomax-cinemas.json"))
	if err != nil {
		t.Fatalf("разбор справочника: %v", err)
	}
	rows := registryRows(t, func(n string) bool { return n != "Москино" })

	obs := buildCinemaObservations(applyCityScope(rows), "2026-08-03T10:00:00Z")
	res := bindNetworkVenues(obs, vs)

	if res.Bound != 6 {
		t.Errorf("привязано %d, ожидалось 6", res.Bound)
	}

	var clones, bound int
	for _, o := range obs {
		if o.Fields[fStatusClass] == classCloneOf {
			clones++
			if strings.HasPrefix(o.Fields[fSourceParams], "venue=") {
				t.Errorf("клон %q получил канал опроса", o.Name)
			}
			continue
		}
		if strings.HasPrefix(o.Fields[fSourceParams], "venue=") {
			bound++
			// В запрос идёт ident, а не число: на числовом id сеть отвечает 404.
			id := strings.TrimPrefix(o.Fields[fSourceParams], "venue=")
			if _, err := strconv.Atoi(id); err == nil {
				t.Errorf("%q получила числовой идентификатор %q вместо ident", o.Name, id)
			}
		}
	}
	if clones != 14 {
		t.Errorf("клонов %d, ожидалось 14", clones)
	}
	if bound != 6 {
		t.Errorf("привязанных строк %d, ожидалось 6", bound)
	}
}

// Живые названия ломают наивное сравнение тремя способами разом — тест держит
// все три, потому что каждый встретился в реестре.
func TestVenueKeyHandlesLiveNames(t *testing.T) {
	cases := []struct{ registry, directory, note string }{
		{`«Киномакс-Водный» г. Москва`, "Киномакс-Водный Москва", "кавычки и хвост с городом"},
		{`Государственное бюджетное учреждение культуры города Москвы "Московское кино", кинотеатр "Москино Юность"`,
			"Юность", "юридическая обёртка вокруг названия"},
		{"Москино Березка", "Берёзка (временно закрыт на ремонт)", "приписка о состоянии и ёфикация"},
	}
	for _, c := range cases {
		if got, want := venueKey(c.registry), venueKey(c.directory); got != want {
			t.Errorf("%s: ключи разошлись — %q → %q, %q → %q", c.note, c.registry, got, c.directory, want)
		}
	}
}

func TestParseCinemaStarVenues(t *testing.T) {
	vs, err := parseCinemaStarVenues(readFixture(t, "cinemastar-venues.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(vs) < 3 {
		t.Fatalf("площадок %d, в фикстуре их больше", len(vs))
	}
	for _, v := range vs {
		if v.ID == "" || v.Name == "" {
			t.Errorf("площадка без uid или названия: %+v", v)
		}
	}

	// Областная площадка остаётся в справочнике: охват решает реестр, а не
	// второе правило отсева внутри разбора.
	var hasRegional bool
	for _, v := range vs {
		if strings.Contains(strings.ToLower(v.Name), "реутов") {
			hasRegional = true
		}
	}
	if !hasRegional {
		t.Error("областная площадка отфильтрована внутри разбора — охват решается не здесь")
	}
}

func TestParseCinemaParkVenues(t *testing.T) {
	vs, err := parseCinemaParkVenues(readFixture(t, "cinemapark-venues.html"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(vs) < 3 {
		t.Fatalf("площадок %d, в фикстуре их больше", len(vs))
	}

	// Одна сеть держит четыре вывески, и в фикстуре их минимум две.
	brands := map[string]bool{}
	for _, v := range vs {
		low := strings.ToLower(v.Name)
		switch {
		case strings.Contains(low, "синема парк"):
			brands["cinemapark"] = true
		case strings.Contains(low, "формула кино"):
			brands["formula"] = true
		case strings.Contains(low, "кронверк"):
			brands["kronverk"] = true
		case strings.Contains(low, "okko"), strings.Contains(low, "оkkо"):
			brands["okko"] = true
		}
	}
	if len(brands) < 2 {
		t.Errorf("вывесок в фикстуре %d, нужно не меньше двух: %v", len(brands), brands)
	}

	if _, err := parseCinemaParkVenues("<html><body>нет ссылок</body></html>"); err == nil {
		t.Error("разбор промолчал об отсутствии ссылок")
	}
}

// Четыре вывески одной сети снимаются перед сравнением — иначе не совпала бы
// ни одна площадка СИНЕМА ПАРК.
func TestVenueKeyStripsCinemaParkBrands(t *testing.T) {
	cases := []struct{ registry, directory string }{
		{"Синема Парк Мосфильм", "Синема Парк Мосфильм"},
		{"Кронверк Синема Вэйпарк", "Кронверк Синема Вэйпарк"},
		{"Кино Оkkо Щёлковский", "Кино Оkkо Щёлковский"},
		{"Формула Кино ЦДМ", "Формула Кино ЦДМ"},
	}
	for _, c := range cases {
		got := venueKey(c.registry)
		if got == "" || strings.Contains(got, "синема") || strings.Contains(got, "кино") {
			t.Errorf("вывеска не снята: %q → %q", c.registry, got)
		}
	}
}

// Списки, заданные в коде, обязаны совпадать по размеру с тем, что есть в
// реестре: три площадки «Пяти звёзд» и два найденных uuid у p24.
func TestHardcodedVenueLists(t *testing.T) {
	if len(fiveStarsVenues) != 3 {
		t.Errorf("площадок «Пяти звёзд» %d, в реестре их три", len(fiveStarsVenues))
	}
	if len(p24Venues) != 2 {
		t.Errorf("uuid p24 %d, найдено было два", len(p24Venues))
	}
	for _, v := range append(append([]NetworkVenue{}, fiveStarsVenues...), p24Venues...) {
		if v.ID == "" || v.Name == "" || v.Kind == "" {
			t.Errorf("неполная запись: %+v", v)
		}
	}
}

// Отказ одного справочника не рушит прогон и не калечит остальные сети.
//
// Свойство несущее: справочники живут на чужих серверах, любой из них может
// ответить 500 или сменить вёрстку. Если такой отказ уронит весь шаг, прогон
// потеряет каналы всех сетей разом — из-за одной.
func TestBindAllNetworksSurvivesBrokenDirectory(t *testing.T) {
	obs := buildCinemaObservations(applyCityScope([]EaisRow{
		{ID: "1", City: "Москва г", Company: "КАРО 7 Атриум", Network: "КАРО ФИЛЬМ"},
		{ID: "2", City: "Москва г", Company: "Пять звёзд на Смоленской", Network: "Пять звезд"},
	}), "2026-08-03T10:00:00Z")

	// КАРО отвечает мусором, остальные сетевые справочники — пустотой.
	fetch := func(url string) (string, error) {
		if url == karoDirectoryURL {
			return "не JSON вовсе", nil
		}
		return "", errNoDirectory
	}

	res := bindAllNetworks(fetch, obs)
	if len(res) != len(networkDirectories) {
		t.Fatalf("сетей в отчёте %d, справочников %d", len(res), len(networkDirectories))
	}

	byName := map[string]NetworkBinding{}
	for _, b := range res {
		byName[b.Network] = b
	}
	if byName["КАРО"].Error == "" {
		t.Error("сломанный справочник КАРО не отмечен ошибкой")
	}
	if byName["КАРО"].Bound != 0 {
		t.Errorf("сломанный справочник всё же что-то привязал: %d", byName["КАРО"].Bound)
	}
	// Список в коде от сети не зависит вовсе — он обязан отработать.
	if byName["Пять звёзд"].Bound != 1 {
		t.Errorf("«Пять звёзд» привязали %d строк, ожидалась одна", byName["Пять звёзд"].Bound)
	}
	if obs[1].Fields[fSourceKind] != kind5Zvezd {
		t.Errorf("площадка «Пяти звёзд» осталась без канала: %q", obs[1].Fields[fSourceKind])
	}
	// Строка КАРО осталась непокрытой, но не получила вердикта по справочнику,
	// которого прогон так и не увидел.
	if obs[0].Fields[fSourceKind] != "" {
		t.Errorf("строка КАРО получила канал из сломанного справочника: %q", obs[0].Fields[fSourceKind])
	}
	if obs[0].Fields[fLastError] != "" {
		t.Errorf("строка КАРО получила вердикт по неполученному справочнику: %q", obs[0].Fields[fLastError])
	}
}

var errNoDirectory = fmt.Errorf("справочник недоступен")
