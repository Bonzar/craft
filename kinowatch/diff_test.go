package main

// Тесты сравнения прогонов.
//
// Сравнение — единственное место, где инструмент отвечает на вопрос «что
// изменилось сегодня», и единственное, которое может тихо соврать: снимок по
// чужому фильму или по чужому окну даёт правдоподобную, но выдуманную разницу.

import (
	"strings"
	"testing"
)

func runOf(at string, days int, title string, venues ...VenueProbe) ProbeReport {
	return ProbeReport{FetchedAt: at, Days: days, Film: FilmProfile{Title: title}, Venues: venues}
}

func showing(key, name, day string, times ...string) VenueProbe {
	vp := VenueProbe{Key: key, Name: name, SaleFrom: day}
	for _, t := range times {
		vp.Found = append(vp.Found, FoundShowtime{
			Title: "Человек-паук: Новый день", StartsAt: day + "T" + t + ":00+03:00", Hall: "Зал 1", Format: "2D",
		})
	}
	return vp
}

// Площадка, у которой сеансов не было, а теперь есть, — это и есть искомый
// сигнал «продажа открылась».
func TestDiffRunsSeesOpening(t *testing.T) {
	prev := runOf("2026-08-04T09:00:00Z", 28, "Человек-паук: Новый день",
		VenueProbe{Key: "7458", Name: "ЗигЗаг"})
	cur := runOf("2026-08-05T09:00:00Z", 28, "Человек-паук: Новый день",
		showing("7458", "ЗигЗаг", "2026-08-20", "10:10", "11:20"))

	got := diffRuns(cur, prev)

	if got.Skipped != "" {
		t.Fatalf("сравнение не сделано: %s", got.Skipped)
	}
	if len(got.Opened) != 1 || got.Opened[0].Key != "7458" {
		t.Fatalf("открытие продажи не поймано: %+v", got.Opened)
	}
	if got.Opened[0].SaleFrom != "2026-08-20" {
		t.Errorf("дата открытия %q, ожидалось 2026-08-20", got.Opened[0].SaleFrom)
	}
	// Дата снимка печатается всегда: разница со вчерашним снимком и с
	// трёхдневным — разного веса.
	if got.PreviousAt != prev.FetchedAt {
		t.Errorf("дата прошлого прогона потеряна: %q", got.PreviousAt)
	}
}

// Два одинаковых прогона не дают разницы — иначе сигнал ничего не стоит.
func TestDiffRunsSameSnapshotIsQuiet(t *testing.T) {
	r := runOf("2026-08-05T09:00:00Z", 28, "Человек-паук: Новый день",
		showing("7458", "ЗигЗаг", "2026-08-20", "10:10"))

	got := diffRuns(r, r)

	if len(got.Opened)+len(got.Extended)+len(got.Gone) != 0 {
		t.Errorf("сравнение снимка с самим собой дало разницу: %+v", got)
	}
}

// Снимок по другому фильму сравнение отклоняет.
//
// Живой случай: лежащий в репозитории .last-run.json снят по «Ограбить Лондон».
// Молчаливое сравнение с ним показало бы «открылось у всех», и это выглядело бы
// как настоящая находка.
func TestDiffRunsRejectsOtherFilm(t *testing.T) {
	prev := runOf("2026-08-04T09:00:00Z", 28, "Ограбить Лондон",
		showing("7458", "ЗигЗаг", "2026-08-05", "10:00"))
	cur := runOf("2026-08-05T09:00:00Z", 28, "Человек-паук: Новый день",
		showing("7458", "ЗигЗаг", "2026-08-20", "10:10"))

	got := diffRuns(cur, prev)

	if got.Skipped == "" {
		t.Fatal("снимок по другому фильму принят к сравнению")
	}
	if len(got.Opened) != 0 {
		t.Errorf("из чужого снимка выведены открытия: %+v", got.Opened)
	}
	if !strings.Contains(got.Skipped, "Ограбить Лондон") {
		t.Errorf("в причине не видно чужого фильма: %s", got.Skipped)
	}
}

// Снимок с более узким окном сравнивается по пересечению, а не отвергается.
//
// Требовать равенства окон нельзя: прошлый снимок снят на другом горизонте, и
// такое правило отключило бы весь раздел на первом же прогоне.
func TestDiffRunsComparesOnOverlap(t *testing.T) {
	// Прошлый прогон смотрел один день — только 04.08.
	prev := runOf("2026-08-04T09:00:00Z", 1, "Человек-паук: Новый день",
		VenueProbe{Key: "7458", Name: "ЗигЗаг"})
	// Текущий смотрит месяц и нашёл сеансы 20 августа — вне пересечения.
	cur := runOf("2026-08-04T21:00:00Z", 28, "Человек-паук: Новый день",
		showing("7458", "ЗигЗаг", "2026-08-20", "10:10"))

	got := diffRuns(cur, prev)

	if got.Skipped != "" {
		t.Fatalf("сравнение отвергнуто вместо пересечения: %s", got.Skipped)
	}
	if got.OverlapFrom != "2026-08-04" || got.OverlapTo != "2026-08-04" {
		t.Errorf("пересечение %q…%q, ожидалось 2026-08-04…2026-08-04", got.OverlapFrom, got.OverlapTo)
	}
	// Сеансы 20 августа лежат вне пересечения: прошлый прогон туда не смотрел,
	// и объявлять их «открывшимися» нельзя — это было бы выводом о том, чего
	// снимок не видел.
	if len(got.Opened) != 0 {
		t.Errorf("сеансы вне пересечения окон объявлены открытием: %+v", got.Opened)
	}
}

// Исчезновение сеансов — обратный сигнал, и он тоже нужен: снятие с проката или
// поломка канала.
func TestDiffRunsSeesDisappearance(t *testing.T) {
	prev := runOf("2026-08-04T09:00:00Z", 28, "Одиссея",
		showing("1", "Художественный", "2026-08-06", "19:00"))
	cur := runOf("2026-08-05T09:00:00Z", 28, "Одиссея",
		VenueProbe{Key: "1", Name: "Художественный"})

	got := diffRuns(cur, prev)

	if len(got.Gone) != 1 || got.Gone[0].Key != "1" {
		t.Errorf("исчезновение сеансов не поймано: %+v", got)
	}
}

// Первый прогон сравнивать не с чем, и это не ошибка.
func TestDiffRunsWithoutSnapshot(t *testing.T) {
	got := diffRuns(runOf("2026-08-05T09:00:00Z", 28, "Одиссея"), ProbeReport{})

	if got.Skipped == "" {
		t.Error("отсутствие снимка не объяснено")
	}
}

// Сеанс, заменённый другим в то же время, но в другом формате, — это изменение.
// Сравнение по числам его бы не заметило.
func TestDiffRunsCountsFormatAsDistinct(t *testing.T) {
	prev := runOf("2026-08-05T09:00:00Z", 28, "Одиссея",
		VenueProbe{Key: "1", Name: "КАРО", Found: []FoundShowtime{
			{Title: "Одиссея", StartsAt: "2026-08-06T19:00:00+03:00", Hall: "Зал 1", Format: "2D"},
		}})
	cur := runOf("2026-08-05T10:00:00Z", 28, "Одиссея",
		VenueProbe{Key: "1", Name: "КАРО", Found: []FoundShowtime{
			{Title: "Одиссея", StartsAt: "2026-08-06T19:00:00+03:00", Hall: "Зал 1", Format: "2D"},
			{Title: "Одиссея", StartsAt: "2026-08-06T19:00:00+03:00", Hall: "Зал 1", Format: "IMAX"},
		}})

	got := diffRuns(cur, prev)

	if len(got.Extended) != 1 {
		t.Errorf("добавленный сеанс в другом формате не замечен: %+v", got)
	}
}
