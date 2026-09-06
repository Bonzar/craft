package main

import (
	"strings"
	"testing"
)

// Приоритет классов — не косметика: он решает, попадёт ли площадка в
// знаменатель покрытия. Тест держит именно это свойство, а не порядок ради
// порядка.
func TestPickClassPriority(t *testing.T) {
	cases := []struct {
		name    string
		reasons []string
		want    string
	}{
		{"пусто", nil, ""},
		{"один класс", []string{classUncovered}, classUncovered},
		{
			"сайт не найден важнее ненаписанного адаптера",
			[]string{classUncovered, classSiteUnknown},
			classSiteUnknown,
		},
		{
			"мёртвый домен важнее несошедшегося геокода: он объективен",
			[]string{classNoSource, classGeoUnknown},
			classNoSource,
		},
		{
			"нет онлайн-продажи важнее ненаписанного адаптера",
			[]string{classUncovered, classNoOnlineSale},
			classNoOnlineSale,
		},
		{
			"сезонность важнее ненаписанного адаптера: вне сезона расписания нет ни при каком адаптере",
			[]string{classUncovered, classSeasonal},
			classSeasonal,
		},
		{
			"клон сильнее всего: строка вообще не описывает отдельную площадку",
			[]string{classNoSource, classSeasonal, classCloneOf},
			classCloneOf,
		},
		{"пустые причины игнорируются", []string{"", classSeasonal, ""}, classSeasonal},
	}

	for _, c := range cases {
		if got := pickClass(c.reasons...); got != c.want {
			t.Errorf("%s: pickClass(%v) = %q, want %q", c.name, c.reasons, got, c.want)
		}
	}
}

// Знаменатель покрытия обязан сохранять всё, что про НАШУ неготовность.
// Иначе процент считался бы от удобной выборки: не нашли сайт — выкинули из
// знаменателя — покрытие 100%.
func TestKeepsInDenominator(t *testing.T) {
	keep := []string{classSiteUnknown, classGeoUnknown, classUncovered, ""}
	drop := []string{classNoSource, classNoOnlineSale, classSeasonal, classCloneOf}

	for _, c := range keep {
		if !keepsInDenominator(c) {
			t.Errorf("класс %q должен оставаться в знаменателе: он про нашу неготовность", c)
		}
	}
	for _, c := range drop {
		if keepsInDenominator(c) {
			t.Errorf("класс %q не должен считаться в знаменателе: он про саму площадку", c)
		}
	}
}

// Объективное препятствие всегда сильнее нашей неготовности.
//
// Смысл в честности метрики в обе стороны: площадку, которую нельзя опросить в
// принципе (мёртвый домен, музей без билетной системы, закрытый до лета
// летний кинотеатр), незачем держать в знаменателе — покрытие иначе никогда не
// сойдётся по причинам вне нашего контроля. И наоборот: пока препятствие наше,
// площадка обязана остаться в знаменателе и мозолить глаза.
func TestObjectiveReasonsOutrankOurOwnGaps(t *testing.T) {
	objective := []string{classNoSource, classNoOnlineSale, classSeasonal}
	ours := []string{classSiteUnknown, classGeoUnknown, classUncovered}

	for _, o := range objective {
		for _, m := range ours {
			got := pickClass(m, o)
			if got != o {
				t.Errorf("совпали наше %q и объективное %q → выбрано %q, должно быть %q",
					m, o, got, o)
			}
			if keepsInDenominator(got) {
				t.Errorf("объективная причина %q обязана выводить площадку из знаменателя", got)
			}
		}
	}

	// Обратная сторона: если объективных препятствий нет, побеждает наше — и
	// площадка остаётся в знаменателе как невыполненная работа.
	for i := range ours {
		for j := range ours {
			if got := pickClass(ours[i], ours[j]); !keepsInDenominator(got) {
				t.Errorf("совпали %q и %q → выбран %q, площадка выпала из знаменателя",
					ours[i], ours[j], got)
			}
		}
	}
}

// Note накапливает, а не перезаписывается: пометки ставят разные шаги в разное
// время, и первая же ежечасная запись затёрла бы вчерашнее предупреждение.
func TestAddNoteAccumulatesAndDeduplicates(t *testing.T) {
	fields := map[string]string{}

	addNote(fields, noteGeoNameDup)
	addNote(fields, noteGeoUnverified)
	addNote(fields, noteGeoNameDup) // повтор от другого шага

	got := fields[fNote]
	if !strings.Contains(got, noteGeoNameDup) || !strings.Contains(got, noteGeoUnverified) {
		t.Errorf("пометка потеряна: %q", got)
	}
	if strings.Count(got, noteGeoNameDup) != 1 {
		t.Errorf("повторная пометка задвоилась: %q", got)
	}

	// Пустая пометка поля не создаёт — иначе в реестре появился бы пустой Note.
	clean := map[string]string{}
	addNote(clean, "", "  ")
	if _, ok := clean[fNote]; ok {
		t.Errorf("пустые пометки создали поле: %q", clean[fNote])
	}
}

// GeoAt ставится на каждом решении, даже когда запросов не было вовсе. Иначе
// площадка вечно подходит под условие «раньше не пробовалась» и штурмует
// геокодер каждый час.
func TestApplyGeoAlwaysStampsAttemptTime(t *testing.T) {
	now := "2026-07-31T10:00:00Z"

	failed := CinemaObservation{Fields: map[string]string{fStatusClass: classUncovered}}
	applyGeo(&failed, GeoOutcome{Evidence: "https://example/api?q=x"}, now)

	if failed.Fields[fGeoAt] != now {
		t.Errorf("время попытки не проставлено: %q", failed.Fields[fGeoAt])
	}
	if failed.Fields[fStatusClass] != classGeoUnknown {
		t.Errorf("класс после неудачи %q, ожидался %q", failed.Fields[fStatusClass], classGeoUnknown)
	}
	if failed.Fields[fEvidenceURL] == "" {
		t.Error("URL последней попытки потерян — неудачу нечем перепроверить")
	}
	// InsideMkad остаётся пустым: false молча вычеркнул бы площадку из охвата.
	if v, ok := failed.Fields[fInsideMkad]; ok && v != "" {
		t.Errorf("InsideMkad заполнен без координат: %q", v)
	}
}

func TestApplyGeoWritesPoint(t *testing.T) {
	now := "2026-07-31T10:00:00Z"
	obs := CinemaObservation{Fields: map[string]string{fStatusClass: classUncovered}}

	applyGeo(&obs, GeoOutcome{
		Point: &GeoPoint{
			Lat: 55.748015, Lon: 37.645022,
			Step: stepPhotonTitle, Address: "Москва, Большой Ватин переулок",
			Evidence: "https://photon/api?q=y",
		},
		Notes: []string{noteGeoUnverified},
	}, now)

	if obs.Fields[fLat] != "55.748015" || obs.Fields[fLon] != "37.645022" {
		t.Errorf("координаты записаны как %q, %q", obs.Fields[fLat], obs.Fields[fLon])
	}
	if obs.Fields[fGeoStep] != stepPhotonTitle {
		t.Errorf("ступень не записана: %q", obs.Fields[fGeoStep])
	}
	if obs.Fields[fAddress] == "" {
		t.Error("добытый адрес не сохранён — следующий прогон решал бы ту же задачу заново")
	}
	if obs.Fields[fNote] != noteGeoUnverified {
		t.Errorf("пометка неподтверждённого геокода потеряна: %q", obs.Fields[fNote])
	}
	// Успех не превращает площадку в geo_unknown.
	if obs.Fields[fStatusClass] == classGeoUnknown {
		t.Error("площадка с координатами помечена как geo_unknown")
	}
}

// Готовые координаты обогатителя снимают нужду в запросах вовсе — это главная
// экономия прогона.
func TestSetEnrichedMarksSourceAndSkipsGeocoder(t *testing.T) {
	now := "2026-07-31T10:00:00Z"
	obs := CinemaObservation{Fields: map[string]string{}}

	setEnriched(&obs, EnrichedVenue{
		Name: "Иллюзион", Address: "Москва, Большой Ватин переулок",
		Lat: 55.748015, Lon: 37.645022, Source: "osm", Website: "https://illuzion.example",
	}, now)

	if !hasCoords(obs) {
		t.Fatal("координаты обогатителя не записаны")
	}
	if obs.Fields[fGeoStep] != "enricher:osm" {
		t.Errorf("источник координат не виден: %q", obs.Fields[fGeoStep])
	}
	if obs.Fields[fSiteURL] == "" {
		t.Error("сайт из тегов OSM потерян")
	}
}

func TestBuildCinemaObservations(t *testing.T) {
	decisions := []ScopeDecision{
		{Row: EaisRow{ID: "6038", Company: "Иллюзион", City: "Москва г"}, InScope: true},
		{Row: EaisRow{ID: "1923", Company: "Киностар Де Люкс", City: "Мамыри д", Network: "СИНЕМА ПАРК"},
			InScope: false, Reason: "tinao"},
		{Row: EaisRow{ID: "8656", Company: "КАРО под звёздами", City: "Москва г", Network: "КАРО ФИЛЬМ"},
			InScope: true},
	}

	obs := buildCinemaObservations(decisions, "2026-07-31T10:00:00Z")

	if len(obs) != 2 {
		t.Fatalf("наблюдений %d, ожидалось 2 (строка вне охвата в реестр не пишется)", len(obs))
	}

	if obs[0].Key != "6038" || obs[0].Name != "Иллюзион" {
		t.Errorf("первое наблюдение: key=%q name=%q", obs[0].Key, obs[0].Name)
	}

	// Ключи свойств — схлопнутые, без подчёркиваний: Craft их стирает.
	if _, ok := obs[0].Fields["statusclass"]; !ok {
		t.Errorf("ключ статуса должен быть схлопнутым 'statusclass', получено: %v", obs[0].Fields)
	}

	// Пустая площадка не молчит: она сразу непокрытая, а значит видна в
	// знаменателе как наша недоработка.
	if got := obs[0].Fields[fStatusClass]; got != classUncovered {
		t.Errorf("свежая площадка должна получить %q, получено %q", classUncovered, got)
	}
	if !keepsInDenominator(obs[0].Fields[fStatusClass]) {
		t.Error("свежая площадка обязана остаться в знаменателе покрытия")
	}

	if got := obs[1].Fields[fNetwork]; got != "КАРО ФИЛЬМ" {
		t.Errorf("сеть не перенесена: %q", got)
	}
}

// Клоны реестра: три записи ЕАИС на один набор из семи залов. Опрашивать их
// порознь нельзя — один физический сеанс записался бы трижды с тремя разными
// ключами, и дедуп их не схлопнул бы, потому что ключи честно разные.
func TestCloneNetworksGetLeaderAndLeaveDenominator(t *testing.T) {
	rows := []EaisRow{
		{ID: "1", City: "Москва г", Company: "Киномакс Водный", Network: `АО "Киномакс" в г. Москва`},
		{ID: "2", City: "Москва г", Company: "Кинотеатр в ТЦ", Network: "Созвездие"},
		{ID: "3", City: "Москва г", Company: "Кинотеатр в ТРК", Network: "Кинообслуживание"},
	}

	obs := buildCinemaObservations(applyCityScope(rows), "2026-08-01T10:00:00Z")
	if len(obs) != 3 {
		t.Fatalf("наблюдений %d, ожидалось 3: клон остаётся в реестре видимой строкой", len(obs))
	}

	// Ведущая опрашивается как обычно.
	if got := obs[0].Fields[fStatusClass]; got != classUncovered {
		t.Errorf("ведущая запись получила класс %q вместо %q", got, classUncovered)
	}
	if obs[0].Fields[fSourceParams] != "" {
		t.Errorf("ведущей проставлена ссылка на ведущую: %q", obs[0].Fields[fSourceParams])
	}

	for _, i := range []int{1, 2} {
		if got := obs[i].Fields[fStatusClass]; got != classCloneOf {
			t.Errorf("клон %q получил класс %q, ожидался %q", obs[i].Fields[fNetwork], got, classCloneOf)
		}
		if !strings.Contains(obs[i].Fields[fSourceParams], "Киномакс") {
			t.Errorf("у клона %q нет ссылки на ведущую: %q", obs[i].Fields[fNetwork], obs[i].Fields[fSourceParams])
		}
		if keepsInDenominator(obs[i].Fields[fStatusClass]) {
			t.Errorf("клон %q остался в знаменателе — метрика считала бы фантомы", obs[i].Fields[fNetwork])
		}
	}
}

// Обратная сторона: сеть, не входящая в карту клонов, схлопыванию не подлежит.
// Карта ручная именно потому, что похожесть названий основанием не является.
func TestNonCloneNetworkIsUntouched(t *testing.T) {
	if leader := cloneLeader("КАРО ФИЛЬМ"); leader != "" {
		t.Errorf("обычная сеть объявлена клоном сети %q", leader)
	}
	if leader := cloneLeader("Созвездие"); leader == "" {
		t.Error("известный клон не опознан")
	}
}

// Площадка без кинопоказов получает объективный класс, а не «адаптер не
// написан»: разница в том, попадёт ли она в знаменатель покрытия навсегда.
func TestVenuesWithoutScreeningsLeaveDenominator(t *testing.T) {
	rows := []EaisRow{
		{ID: "10", City: "Москва г", Company: "Коперто"},
		{ID: "11", City: "Москва г", Company: `Киноклуб-музей "Эльдар"`},
		{ID: "12", City: "Москва г", Company: "Художественный"},
	}

	obs := buildCinemaObservations(applyCityScope(rows), "2026-08-03T10:00:00Z")
	if len(obs) != 3 {
		t.Fatalf("наблюдений %d, ожидалось 3", len(obs))
	}

	for _, i := range []int{0, 1} {
		got := obs[i].Fields[fStatusClass]
		if got != classNoOnlineSale {
			t.Errorf("%q получил класс %q, ожидался %q", obs[i].Name, got, classNoOnlineSale)
		}
		if keepsInDenominator(got) {
			t.Errorf("%q остался в знаменателе — площадка без сеансов вечно висела бы недоработкой", obs[i].Name)
		}
		if obs[i].Fields[fLastError] == "" {
			t.Errorf("%q не несёт причину словами — основание не видно без чтения кода", obs[i].Name)
		}
		if obs[i].Fields[fStatusAt] == "" {
			t.Errorf("%q без даты решения — месячная перепроверка не сработает", obs[i].Name)
		}
	}

	// Обычная площадка остаётся непокрытой, то есть в знаменателе.
	if got := obs[2].Fields[fStatusClass]; got != classUncovered {
		t.Errorf("обычная площадка получила класс %q вместо %q", got, classUncovered)
	}
	if !keepsInDenominator(obs[2].Fields[fStatusClass]) {
		t.Error("обычная площадка выпала из знаменателя")
	}
}

// Совпадение только по подстроке названия, а не по любому созвучию: чужая
// площадка не должна нечаянно получить чужой класс.
func TestScreeningsAbsentReasonIsNarrow(t *testing.T) {
	if r := screeningsAbsentReason("Коперто"); r == "" {
		t.Error("известная площадка без сеансов не опознана")
	}
	for _, name := range []string{"Художественный", "ГУМ Кинозал", "Пять звёзд", "КАРО ФИЛЬМ"} {
		if r := screeningsAbsentReason(name); r != "" {
			t.Errorf("обычной площадке %q приписано отсутствие сеансов: %q", name, r)
		}
	}
}
