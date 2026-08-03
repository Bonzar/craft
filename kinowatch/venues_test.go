package main

// Тесты привязки площадок к каналам их сетей.
//
// Проверяется главное свойство: у КАЖДОЙ строки реестра есть исход. Молчаливо
// пропущенная строка — это площадка, которую прогон никогда не опросит, и
// заметить это по отчёту будет нельзя.

import (
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

	// У каждой строки исход: либо канал, либо явная причина его отсутствия.
	for _, o := range obs {
		params := o.Fields[fSourceParams]
		class := o.Fields[fStatusClass]
		if params == "" && class != classNoSource {
			t.Errorf("%q осталась без исхода: params=%q class=%q", o.Name, params, class)
		}
		if params != "" && !strings.HasPrefix(params, "venue=") {
			t.Errorf("%q: параметры канала записаны как %q", o.Name, params)
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
	}
	for _, c := range cases {
		if got, want := venueKey(c.registry), venueKey(c.directory); got != want {
			t.Errorf("ключи разошлись: %q → %q, %q → %q", c.registry, got, c.directory, want)
		}
	}
}
