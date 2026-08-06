package main

// Тесты привязки площадок агрегатора к строкам реестра.
//
// Все случаи взяты из живого замера 05.08.2026 по «Майклу»: и верные пары, и
// обе ложные. Синтетики тут нет намеренно — правила писались под конкретные
// промахи, и проверять их надо на них же.

import (
	"fmt"
	"strings"
	"testing"
)

// row — строка реестра с координатами или без.
func row(key, name string, lat, lon string) CinemaObservation {
	o := CinemaObservation{Key: key, Name: name, Fields: map[string]string{}}
	if lat != "" {
		o.Fields[fLat], o.Fields[fLon] = lat, lon
	}
	return o
}

func bucketOf(t *testing.T, res AttachResult, venueID string) UnattachedVenue {
	t.Helper()
	for _, u := range res.Unattached {
		if u.Venue.ID == venueID {
			return u
		}
	}
	t.Fatalf("площадка %q не попала ни в одну корзину — она растворилась", venueID)
	return UnattachedVenue{}
}

func attachedOf(t *testing.T, res AttachResult, venueID string) VenueAttachment {
	t.Helper()
	for _, a := range res.Attached {
		if a.Venue.ID == venueID {
			return a
		}
	}
	t.Fatalf("площадка %q не привязалась", venueID)
	return VenueAttachment{}
}

// Ближняя пара привязывается, а сосед в серой зоне уходит в спорные.
//
// Живой случай: «Художественный» стоит в 2 метрах от своей строки реестра, а
// «Киноцентр «Домжур»» — в 157 метрах от НЕЁ ЖЕ. Оба московские, оба реальные,
// и без серой зоны Домжур сел бы на чужую строку.
func TestAttachGrayZoneKeepsNeighbourOut(t *testing.T) {
	obs := []CinemaObservation{row("1", "Художественный", "55.752286", "37.601494")}
	venues := []AggregatorVenue{
		{ID: "hud", Title: "Художественный", Address: "Арбатская пл., 14, стр. 1", Lat: 55.752300, Lon: 37.601500},
		{ID: "domjur", Title: "Киноцентр «Домжур»", Address: "Никитский бульв., 8а", Lat: 55.753500, Lon: 37.599900},
	}

	res := attachVenues(venues, obs)

	if got := attachedOf(t, res, "hud"); got.RegistryName != "Художественный" || got.By != "coords" {
		t.Errorf("ближняя пара привязана неверно: %+v", got)
	}
	dj := bucketOf(t, res, "domjur")
	if dj.Bucket != bucketDisputed {
		t.Errorf("Домжур попал в корзину %q, ожидалась %q: %s", dj.Bucket, bucketDisputed, dj.Reason)
	}
	if !strings.Contains(dj.Reason, "Художественный") {
		t.Errorf("в причине не видно, с кем спор: %s", dj.Reason)
	}
}

// Одинокий претендент в серой зоне тоже не привязывается.
//
// Это отдельный случай, и он опаснее первого: конкурента нет, возразить некому.
// Живой пример — «Coperto Cinema» в 279 метрах от библиотеки «Москино
// Жуковский»: разные заведения, а по расстоянию похоже на попадание.
func TestAttachLoneGrayCandidateIsDisputed(t *testing.T) {
	obs := []CinemaObservation{row("2", "библиотека «Москино Жуковский»", "55.757800", "37.646500")}
	venues := []AggregatorVenue{
		{ID: "coperto", Title: "Coperto Cinema", Address: "Покровский бульв., 5, пом. 49", Lat: 55.758300, Lon: 37.642200},
	}

	res := attachVenues(venues, obs)

	if len(res.Attached) != 0 {
		t.Fatalf("одинокая площадка из серой зоны привязалась: %+v", res.Attached)
	}
	if got := bucketOf(t, res, "coperto").Bucket; got != bucketDisputed {
		t.Errorf("корзина %q, ожидалась %q", got, bucketDisputed)
	}
}

// На одну строку реестра не садятся две площадки.
//
// Живой случай: «Silver Cinema (Подольск)» и «Silver Cinema (Домодедово)» —
// venueKey снимает скобку с городом, ключ у обеих общий, а строка реестра одна.
// Ловит их отсев по адресу, и именно поэтому он идёт ПЕРВЫМ.
func TestAttachOutOfScopeRunsBeforeNameStep(t *testing.T) {
	obs := []CinemaObservation{row("3", "Silver Cinema", "", "")}
	venues := []AggregatorVenue{
		{ID: "pod", Title: "Silver Cinema (Подольск)", Address: "г. Подольск, ул. Комсомольская, 24, ТРК «Кварц»"},
		{ID: "dom", Title: "Silver Cinema (Домодедово)", Address: "г. Домодедово, Каширское ш., 3а"},
	}

	res := attachVenues(venues, obs)

	if len(res.Attached) != 0 {
		t.Fatalf("подмосковная площадка села на московскую строку: %+v", res.Attached)
	}
	for _, id := range []string{"pod", "dom"} {
		if got := bucketOf(t, res, id).Bucket; got != bucketOutOfScope {
			t.Errorf("%s: корзина %q, ожидалась %q", id, got, bucketOutOfScope)
		}
	}
}

// Московский адрес при загородной точке — своя корзина, а не «не опознано».
//
// Живой случай: «Каро 9 Vegas Каширский» с адресом «24-й км МКАД» — города в
// адресе нет, отсев по тексту его не берёт, а площадка за городом. Проверка
// точки обязана идти ДО именной ступени, иначе имя привяжет её к строке.
func TestAttachOutsideByGeoBeatsNameStep(t *testing.T) {
	obs := []CinemaObservation{row("4", "Каро 9 Vegas Каширский", "", "")}
	venues := []AggregatorVenue{
		{ID: "vegas", Title: "Каро 9 Vegas Каширский", Address: "24-й км МКАД, ТРЦ «Vegas», 2-й этаж",
			Lat: 55.556000, Lon: 37.735000},
	}

	res := attachVenues(venues, obs)

	if len(res.Attached) != 0 {
		t.Fatalf("загородная площадка привязалась по имени: %+v", res.Attached)
	}
	if got := bucketOf(t, res, "vegas").Bucket; got != bucketOutsideByGeo {
		t.Errorf("корзина %q, ожидалась %q", got, bucketOutsideByGeo)
	}
}

// Строка без координат берётся именем — это единственный путь для 96 сетевых
// строк реестра, у которых адреса нет вовсе.
func TestAttachByNameForRowWithoutCoords(t *testing.T) {
	obs := []CinemaObservation{row("5", "«Киномакс-Жулебино» г. Москва", "", "")}
	venues := []AggregatorVenue{
		{ID: "zhul", Title: "Киномакс Жулебино"},
	}

	res := attachVenues(venues, obs)

	got := attachedOf(t, res, "zhul")
	if got.By != "name" {
		t.Errorf("привязка доказана как %q, ожидалось «name»", got.By)
	}
	if got.DistanceM != 0 {
		t.Errorf("у именной привязки появилось расстояние: %d м", got.DistanceM)
	}
}

// Координаты строки главнее имени: совпавшее имя не пересматривает решение,
// принятое расстоянием.
func TestAttachCoordsBeatName(t *testing.T) {
	obs := []CinemaObservation{
		row("6", "Пионер", "55.741000", "37.545000"), // далеко от площадки
	}
	venues := []AggregatorVenue{
		{ID: "pioner", Title: "Пионер", Address: "Кутузовский просп., 21", Lat: 55.760000, Lon: 37.620000},
	}

	res := attachVenues(venues, obs)

	if len(res.Attached) != 0 {
		t.Fatalf("имя перебило координаты строки: %+v", res.Attached)
	}
	// Корзина именно спорная, а не «реестр не знает»: имя-то реестр знает, и
	// смешивать это с площадкой, которой в реестре нет, значит врать в отчёте.
	got := bucketOf(t, res, "pioner")
	if got.Bucket != bucketDisputed {
		t.Errorf("корзина %q, ожидалась %q", got.Bucket, bucketDisputed)
	}
	if !strings.Contains(got.Reason, "6") {
		t.Errorf("в причине не назван eaisid строки: %s", got.Reason)
	}
}

// Инвариант, не зависящий ни от фильма, ни от дня: каждая площадка ровно в
// одном месте, и ни одна строка не получила двух площадок.
func TestAttachEveryVenueLandsExactlyOnce(t *testing.T) {
	obs := []CinemaObservation{
		row("1", "Художественный", "55.752286", "37.601494"),
		row("5", "«Киномакс-Жулебино» г. Москва", "", ""),
	}
	venues := []AggregatorVenue{
		{ID: "hud", Title: "Художественный", Address: "Арбатская пл., 14", Lat: 55.752300, Lon: 37.601500},
		{ID: "domjur", Title: "Киноцентр «Домжур»", Address: "Никитский бульв., 8а", Lat: 55.753500, Lon: 37.599900},
		{ID: "zhul", Title: "Киномакс Жулебино"},
		{ID: "pod", Title: "Silver Cinema (Подольск)", Address: "г. Подольск, ул. Комсомольская, 24"},
	}

	res := attachVenues(venues, obs)

	if n := len(res.Attached) + len(res.Unattached); n != len(venues) {
		t.Errorf("площадок на входе %d, на выходе %d — сумма корзин не сходится", len(venues), n)
	}
	seen := map[string]bool{}
	for _, a := range res.Attached {
		if seen[a.RegistryKey] {
			t.Errorf("строка реестра %q получила вторую площадку: %s", a.RegistryName, a.Venue.Title)
		}
		seen[a.RegistryKey] = true
	}
	place := map[string]int{}
	for _, a := range res.Attached {
		place[a.Venue.ID]++
	}
	for _, u := range res.Unattached {
		place[u.Venue.ID]++
	}
	for id, n := range place {
		if n != 1 {
			t.Errorf("площадка %q встретилась %d раз, ожидался ровно один", id, n)
		}
	}
}

// Сеансы одной площадки сворачиваются в одну запись со счётчиком.
func TestCollectAggregatorVenuesCountsSessions(t *testing.T) {
	got := collectAggregatorVenues([]AggregatorSession{
		{PlaceID: "a", PlaceTitle: "Москино Нева", StartsAt: "2026-08-05T12:50:00+03:00"},
		{PlaceID: "a", PlaceTitle: "Москино Нева", StartsAt: "2026-08-05T15:10:00+03:00"},
		{PlaceID: "b", PlaceTitle: "Кронверк Синема Вэйпарк", StartsAt: "2026-08-05T12:35:00+03:00"},
	})

	if len(got) != 2 {
		t.Fatalf("площадок %d, ожидалось 2", len(got))
	}
	for _, v := range got {
		want := map[string]int{"a": 2, "b": 1}[v.ID]
		if v.Sessions != want {
			t.Errorf("у площадки %q сеансов %d, ожидалось %d", v.ID, v.Sessions, want)
		}
	}
}

// Счётчик расхождений считает по СТРОКАМ реестра, а не по площадкам.
//
// Ради этих трёх чисел второй слой и заводился: сколько площадок нашёл только
// свой канал, сколько только агрегатор и сколько оба. Смешать их с
// непривязанными площадками значило бы выдать «это вообще не наша площадка» за
// «наш канал промолчал».
func TestCountAgreementSplitsThreeWays(t *testing.T) {
	own := map[string]bool{
		"both": true,  // нашли обе стороны
		"own":  true,  // нашёл только свой канал
		"agg":  false, // свой канал промолчал
		"none": false, // не нашёл никто
	}
	byRow := map[string][]AggregatorShowtime{
		"both": {{StartsAt: "2026-08-05T10:00:00+03:00"}},
		"agg":  {{StartsAt: "2026-08-05T12:00:00+03:00"}},
	}

	got := countAgreement(own, byRow)

	want := AgreementStats{OwnOnly: 1, AggregatorOnly: 1, Both: 1}
	if got != want {
		t.Errorf("расхождение посчитано как %+v, ожидалось %+v", got, want)
	}
}

// Строка, которую свой обход вообще не опрашивал, но у агрегатора она есть, —
// это доп-покрытие, и оно обязано быть видно.
func TestCountAgreementSeesRowsOwnRunNeverProbed(t *testing.T) {
	got := countAgreement(map[string]bool{}, map[string][]AggregatorShowtime{
		"uncovered": {{StartsAt: "2026-08-05T10:00:00+03:00"}},
	})

	if got.AggregatorOnly != 1 {
		t.Errorf("доп-покрытие потеряно: %+v", got)
	}
}

// Сеансы агрегатора раскладываются по строкам реестра только через привязку:
// площадка вне корзины «привязано» ничего никуда не приносит.
func TestAggregatorShowtimesByRowFollowsAttachment(t *testing.T) {
	layer := &AggregatorLayer{Attached: []VenueAttachment{
		{Venue: AggregatorVenue{ID: "a"}, RegistryKey: "7912"},
	}}
	sessions := []AggregatorSession{
		{PlaceID: "a", StartsAt: "2026-08-05T20:00:00+03:00", Hall: "1", SaleStatus: "available", PriceMin: 420},
		{PlaceID: "a", StartsAt: "2026-08-05T10:00:00+03:00", Hall: "2"},
		{PlaceID: "b", StartsAt: "2026-08-05T11:00:00+03:00"}, // площадка не привязана
	}

	got := aggregatorShowtimesByRow(layer, sessions)

	if len(got) != 1 || len(got["7912"]) != 2 {
		t.Fatalf("разложено неверно: %+v", got)
	}
	// Порядок по времени, иначе отчёт читается наугад.
	if got["7912"][0].StartsAt > got["7912"][1].StartsAt {
		t.Errorf("сеансы не отсортированы: %+v", got["7912"])
	}
	if got["7912"][1].PriceMin != 420 || got["7912"][1].SaleStatus != "available" {
		t.Errorf("цена и статус продажи потеряны: %+v", got["7912"][1])
	}
}

// ——— Несколько слоёв ———

// Сеансы разных агрегаторов у одной площадки не сливаются в общий список.
//
// Это требование Влада «сетки не объединять», перенесённое на второй уровень:
// как только сеансы двух источников окажутся в одном списке без признака, чей
// он, сравнивать слои станет не с чем.
func TestVenueKeepsAggregatorSessionsPerSource(t *testing.T) {
	vp := VenueProbe{Key: "7458", FromAggregator: map[string][]AggregatorShowtime{}}

	vp.FromAggregator["yandex-afisha"] = []AggregatorShowtime{{StartsAt: "2026-08-20T10:10:00+03:00"}}
	vp.FromAggregator["kinoafisha"] = []AggregatorShowtime{
		{StartsAt: "2026-08-20T10:10:00+03:00"},
		{StartsAt: "2026-08-20T11:20:00+03:00"},
	}

	if len(vp.FromAggregator) != 2 {
		t.Fatalf("источников в отчёте площадки %d, ожидалось 2", len(vp.FromAggregator))
	}
	if len(vp.FromAggregator["yandex-afisha"]) != 1 || len(vp.FromAggregator["kinoafisha"]) != 2 {
		t.Errorf("слои перезаписали друг друга: %+v", vp.FromAggregator)
	}
}

// Счётчик расхождений у каждого слоя свой: один агрегатор видит предпродажу,
// другой по тому же фильму отдаёт ноль, и общим числом это не выразить.
func TestAgreementIsCountedPerLayer(t *testing.T) {
	own := map[string]bool{"7458": false}

	// Первый слой площадку не знает вовсе.
	blind := countAgreement(own, map[string][]AggregatorShowtime{})
	// Второй видит у неё сеансы.
	seeing := countAgreement(own, map[string][]AggregatorShowtime{
		"7458": {{StartsAt: "2026-08-20T10:10:00+03:00"}},
	})

	if blind.AggregatorOnly != 0 {
		t.Errorf("слепой слой насчитал доп-покрытие: %+v", blind)
	}
	if seeing.AggregatorOnly != 1 {
		t.Errorf("зрячий слой доп-покрытие потерял: %+v", seeing)
	}
}

// Форма сеанса общая для всех источников: привязка, сведение и счётчик у слоёв
// одни и те же, разный только способ добыть поля.
func TestAggregatorSessionIsSourceAgnostic(t *testing.T) {
	// Собран вручную, как это сделает разбор любого источника.
	s := AggregatorSession{
		PlaceID: "kinoafisha:8327263", PlaceTitle: "Вики Синема ЗигЗаг",
		PlaceAddress: "ул. Лобненская, 4А", PlaceLat: 55.889471, PlaceLon: 37.537954,
		StartsAt: "2026-08-20T10:10:00+03:00", SaleStatus: "ticket", PriceMin: 450,
	}

	got := collectAggregatorVenues([]AggregatorSession{s})
	if len(got) != 1 || got[0].Title != "Вики Синема ЗигЗаг" {
		t.Fatalf("сеанс чужого источника не свернулся в площадку: %+v", got)
	}
	if got[0].Lat == 0 {
		t.Error("координаты потеряны — привязка по точке работать не будет")
	}
}

// Московский адрес с явным «г. Москва» не считается чужим городом.
//
// Живой промах: адрес «Мягкого кинотеатра Отрада» записан как «г. Москва,
// Пятницкое шоссе, 7-й километр». Правило «адрес начинается с г.» выбросило бы
// московскую площадку в корзину «вне охвата», хотя она московская — просто за
// МКАД, и её место в корзине «за городом по точке».
func TestOutOfScopeByAddressKeepsMoscow(t *testing.T) {
	moscow := []string{
		"г. Москва, Пятницкое шоссе, 7-й километр",
		"Москва, ул. Лобненская, 4А",
		"ул. Кировоградская, д.13А",
		"24-й км МКАД, ТРЦ «Vegas»",
	}
	for _, a := range moscow {
		if outOfScopeByAddress(a) {
			t.Errorf("московский адрес объявлен чужим городом: %q", a)
		}
	}

	foreign := []string{
		"г. Подольск, ул. Комсомольская, 24",
		"Московская обл., г. Королёв, ул. Дзержинского, 26",
		"Панфиловский просп., 6а, Зеленоград",
	}
	for _, a := range foreign {
		if !outOfScopeByAddress(a) {
			t.Errorf("чужой адрес принят за московский: %q", a)
		}
	}
}

// Одна площадка приходит в расписании десятками сеансов, а страница у неё одна.
// Кэш обязан спрашивать источник по разу — иначе слой сам загонит себя под
// частотную защиту, которая у kinoafisha и отвечает теми самыми 403.
func TestKinoafishaGeoAsksSourceOnce(t *testing.T) {
	var calls int
	g := &kinoafishaGeo{fetch: func(id string) (float64, float64, error) {
		calls++
		return 55.75, 37.61, nil
	}}

	for i := 0; i < 3; i++ {
		lat, lon, err := g.point("8327263")
		if err != nil || lat != 55.75 || lon != 37.61 {
			t.Fatalf("заход %d вернул %v, %v, %v", i, lat, lon, err)
		}
	}
	if calls != 1 {
		t.Errorf("запросов к источнику %d, ожидался 1", calls)
	}
}

// Промах запоминается наравне с удачей: повторный заход за тем же отказом стоит
// столько же, сколько первый, а платим мы им частотным лимитом.
func TestKinoafishaGeoRemembersFailure(t *testing.T) {
	var calls int
	g := &kinoafishaGeo{fetch: func(id string) (float64, float64, error) {
		calls++
		return 0, 0, fmt.Errorf("403")
	}}

	for i := 0; i < 3; i++ {
		if _, _, err := g.point("8327263"); err == nil {
			t.Fatalf("заход %d принял отказ за координаты", i)
		}
	}
	if calls != 1 {
		t.Errorf("запросов к источнику %d, ожидался 1", calls)
	}
}

// Площадка без идентификатора страницы — не повод идти в сеть за пустым
// адресом: у источника он ведёт на список кинотеатров, а не на карточку.
func TestKinoafishaGeoRefusesEmptyID(t *testing.T) {
	g := &kinoafishaGeo{fetch: func(id string) (float64, float64, error) {
		t.Fatal("пустой идентификатор ушёл в сеть")
		return 0, 0, nil
	}}
	if _, _, err := g.point(""); err == nil {
		t.Fatal("пустой идентификатор принят")
	}
}

// Живой случай слоя kinoafisha: две московские площадки «Паука».
//
// Замер 05.08.2026 с реальными координатами со страниц кинотеатров и строками
// реестра как они есть. Обе площадки приходят с приклеенной к вывеске сетью —
// «Вики Синема ЗигЗаг», «Мягкий кинотеатр Отрада», — а координат в расписании
// нет вовсе, они дозагружаются отдельно. Без них Отрада уходила в «реестр не
// опознал» вместо «за городом», то есть выглядела дырой покрытия.
func TestAttachKinoafishaVenuesOfSpiderMan(t *testing.T) {
	obs := []CinemaObservation{
		row("7458", "ЗигЗаг", "", ""), // строка реестра без координат
		row("7912", "Каро Октябрь", "55.7449", "37.5866"),
	}
	venues := []AggregatorVenue{
		{ID: "kinoafisha:8327263", Slug: "8327263", Title: "Вики Синема ЗигЗаг",
			Address: "ул. Лобненская, 4А", Lat: 55.889471, Lon: 37.5379549, Sessions: 70},
		{ID: "kinoafisha:8327074", Slug: "8327074", Title: "Мягкий кинотеатр Отрада",
			Address: "г. Москва, Пятницкое шоссе, 7-й километр", Lat: 55.8758005, Lon: 37.331821, Sessions: 47},
	}

	res := attachVenues(venues, obs)
	if len(res.Attached) != 1 || res.Attached[0].RegistryKey != "7458" {
		t.Fatalf("ЗигЗаг не сел на свою строку: %+v", res.Attached)
	}
	if len(res.Unattached) != 1 || res.Unattached[0].Bucket != bucketOutsideByGeo {
		t.Fatalf("Отрада попала не в ту корзину: %+v", res.Unattached)
	}
}

// Клон в кандидаты именной ступени не попадает: он описывает те же залы, что и
// ведущая строка, и своей площадкой не является.
//
// Живой случай: «Колибри Москва» — две строки ЕАИС, 10735 помечена клоном 9962,
// канал у ведущей. Пока клон считался кандидатом, площадка вечно стояла спорной.
func TestAttachNameStepIgnoresClones(t *testing.T) {
	lead := row("9962", "Колибри Москва", "", "")
	clone := row("10735", "Колибри Москва", "", "")
	clone.Fields[fStatusClass] = classCloneOf

	res := attachVenues([]AggregatorVenue{{ID: "kolibri", Title: "Колибри Москва"}},
		[]CinemaObservation{lead, clone})

	got := attachedOf(t, res, "kolibri")
	if got.RegistryKey != "9962" {
		t.Errorf("площадка села на строку %q, ожидалась ведущая 9962", got.RegistryKey)
	}
}

// Несколько живых строк с одним ключом — спор с называнием eaisid, а не «реестр
// не знает»: реестр знает, и даже дважды.
func TestAttachNameStepNamesRivalRows(t *testing.T) {
	res := attachVenues([]AggregatorVenue{{ID: "prime", Title: "Prime Cinema"}},
		[]CinemaObservation{row("8894", "PRIME CINEMA", "", ""), row("10315", "PRIME CINEMA", "", "")})

	got := bucketOf(t, res, "prime")
	if got.Bucket != bucketDisputed {
		t.Fatalf("корзина %q, ожидалась %q", got.Bucket, bucketDisputed)
	}
	for _, id := range []string{"8894", "10315"} {
		if !strings.Contains(got.Reason, id) {
			t.Errorf("в причине не назван eaisid %s: %s", id, got.Reason)
		}
	}
}

// Адресная ступень: улица И дом. Совпадения по одной улице мало — на живых
// данных «Каширское ш., 14, ТЦ «Гудзон»» иначе садится на «Киномакс-Титан».
func TestAttachByAddressNeedsHouse(t *testing.T) {
	obs := []CinemaObservation{row("100", "Киномакс-Титан", "", "")}
	obs[0].Fields[fAddress] = "Москва, Каширское шоссе, 61"

	res := attachVenues([]AggregatorVenue{
		{ID: "gudzon", Title: "Люксор Гудзон", Address: "Каширское ш., 14, ТЦ «Гудзон»"},
	}, obs)

	if len(res.Attached) != 0 {
		t.Fatalf("привязка по одной улице состоялась: %+v", res.Attached)
	}
}

// Совпали улица и дом — привязка доказана, и доказана именно адресом.
func TestAttachByAddressBindsSameHouse(t *testing.T) {
	obs := []CinemaObservation{row("101", "Пионер", "", "")}
	obs[0].Fields[fAddress] = "Москва, Кутузовский проспект, 21"

	res := attachVenues([]AggregatorVenue{
		{ID: "pioner", Title: "Совсем другое имя", Address: "Кутузовский просп., 21"},
	}, obs)

	got := attachedOf(t, res, "pioner")
	if got.By != "address" || got.RegistryKey != "101" {
		t.Errorf("привязка вышла как %+v, ожидалась адресом на строку 101", got)
	}
}

// Псевдоним вывески — решение Влада по конкретной паре, и оно главнее всех
// вычисляемых улик: «Киномакс Пражская» у агрегаторов это строка 6101, которую
// реестр зовёт «Киномакс - Колумбус».
func TestAttachAliasBeatsEverything(t *testing.T) {
	obs := []CinemaObservation{
		row("6101", "Киномакс - Колумбус", "", ""),
		row("200", "Пражская", "", ""), // ложный тёзка вывески
	}

	res := attachVenues([]AggregatorVenue{{ID: "prag", Title: "Киномакс Пражская"}}, obs)

	got := attachedOf(t, res, "prag")
	if got.By != "alias" || got.RegistryKey != "6101" {
		t.Errorf("привязка вышла как %+v, ожидалась псевдонимом на 6101", got)
	}
}

// Протухшая запись псевдонима не молчит: строку убрали из реестра — это видно в
// причине, а не растворяется в общей корзине.
func TestAttachAliasWithoutRowIsVisible(t *testing.T) {
	res := attachVenues([]AggregatorVenue{{ID: "prag", Title: "Киномакс Пражская"}},
		[]CinemaObservation{row("999", "Совсем другая площадка", "", "")})

	got := bucketOf(t, res, "prag")
	if !strings.Contains(got.Reason, "6101") {
		t.Errorf("в причине не назван eaisid из записи: %s", got.Reason)
	}
}
