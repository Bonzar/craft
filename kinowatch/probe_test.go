package main

// Табличный тест классификации — целиком, включая все ветки без находки.
//
// Причина такой полноты одна: цена ошибки здесь несимметрична. Ложный
// `source_broken` стоит одного лишнего взгляда в отчёт, а ложный `absent`
// означает, что инструмент уверенно сказал «билетов нет» в тот час, когда они
// появились. Ради этого и пишется вся конструкция.

import (
	"errors"
	"testing"
	"time"
)

func TestClassifyProbe(t *testing.T) {
	now := time.Date(2026, 7, 31, 12, 0, 0, 0, moscowTZ)
	future := Playbill{Showtimes: []Showtime{
		{Film: "Одиссея", StartsAt: "2026-08-02T19:00:00+03:00"},
		{Film: "Старый орёл", StartsAt: "2026-08-02T21:00:00+03:00"},
	}}

	cases := []struct {
		name      string
		in        ProbeInput
		want      string
		wantAlive bool
	}{
		{
			"сеть не ответила",
			ProbeInput{Err: errors.New("dial tcp: timeout"), Now: now},
			statusBrokenUnreachable, false,
		},
		{
			"протухший токен Kinoplan отвечает 404 «App not found»",
			ProbeInput{HTTPStatus: 404, Now: now},
			statusBrokenAuth, false,
		},
		{
			"geoblock — 403 у Люксора без российского выхода",
			ProbeInput{HTTPStatus: 403, Now: now},
			statusBrokenAuth, false,
		},
		{
			"пятисотка — это транспорт, а не отсутствие фильма",
			ProbeInput{HTTPStatus: 502, Now: now},
			statusBrokenUnreachable, false,
		},
		{
			"сменилась вёрстка: тело есть, структуры нет",
			ProbeInput{HTTPStatus: 200, BodySize: 61000, ParseErr: errors.New("блоки дат не найдены"), Now: now},
			statusBrokenParse, false,
		},
		{
			"HTTP 200 с пустой афишей живостью НЕ является",
			ProbeInput{HTTPStatus: 200, BodySize: 1700, Now: now},
			statusSuspect, false,
		},
		{
			"все сеансы в прошлом — брошенная страница",
			ProbeInput{
				HTTPStatus: 200, BodySize: 40000, Now: now,
				Playbill: Playbill{Showtimes: []Showtime{{Film: "Кино", StartsAt: "2026-06-01T19:00:00+03:00"}}},
			},
			statusSuspect, false,
		},
		{
			"живой источник без искомого фильма — единственный законный absent",
			ProbeInput{HTTPStatus: 200, BodySize: 40000, Playbill: future, Now: now},
			statusAbsent, true,
		},
		{
			"фильм найден, продажи ещё не открыты",
			ProbeInput{HTTPStatus: 200, BodySize: 40000, Playbill: future, FilmFound: true, Now: now},
			statusFound, true,
		},
		{
			"фильм найден и продаётся — цель инструмента",
			ProbeInput{
				HTTPStatus: 200, BodySize: 40000, Playbill: future,
				FilmFound: true, FilmOnSale: true, Now: now,
			},
			statusOnSale, true,
		},
	}

	for _, c := range cases {
		got := classifyProbe(c.in)
		if got.Status != c.want {
			t.Errorf("%s: статус %q, ожидался %q", c.name, got.Status, c.want)
		}
		if got.Alive != c.wantAlive {
			t.Errorf("%s: живость %v, ожидалась %v", c.name, got.Alive, c.wantAlive)
		}
		if got.Evidence == "" {
			t.Errorf("%s: нет обоснования — разбор прогона превратится в гадание", c.name)
		}
	}
}

// Отдельно и явно: ни один статус поломки не имеет права оказаться absent.
// Это тот самый отказ, ради защиты от которого написан весь файл.
func TestBrokenSourceIsNeverAbsent(t *testing.T) {
	now := time.Date(2026, 7, 31, 12, 0, 0, 0, moscowTZ)
	broken := []ProbeInput{
		{Err: errors.New("reset by peer"), Now: now},
		{HTTPStatus: 500, Now: now},
		{HTTPStatus: 403, Now: now},
		{HTTPStatus: 200, ParseErr: errors.New("нет разметки"), Now: now},
		{HTTPStatus: 200, BodySize: 1200, Now: now},
	}
	for _, in := range broken {
		got := classifyProbe(in)
		if got.Status == statusAbsent {
			t.Errorf("сломанный источник классифицирован как absent: %+v", in)
		}
		if got.Alive {
			t.Errorf("сломанный источник признан живым: %+v → %+v", in, got)
		}
	}
}

// Живость доказывается ЧУЖИМИ сеансами не хуже своих: площадка, где нашего
// фильма нет, но идут десять других, — работающая площадка.
func TestAliveIsProvenByAnyFilm(t *testing.T) {
	now := time.Date(2026, 7, 31, 12, 0, 0, 0, moscowTZ)
	got := classifyProbe(ProbeInput{
		HTTPStatus: 200, BodySize: 40000, Now: now,
		Playbill: Playbill{Showtimes: []Showtime{{Film: "Чужое кино", StartsAt: "2026-08-01T19:00:00+03:00"}}},
	})
	if !got.Alive || got.Status != statusAbsent {
		t.Errorf("площадка с чужими сеансами не признана живой: %+v", got)
	}
}

// Молчание нескольких прогонов подряд — это stale, и живёт оно в результате
// прогона, а не в классе площадки: площадка остаётся видимой.
func TestMarkStale(t *testing.T) {
	if got := markStale(3, statusBrokenUnreachable); got != statusStale {
		t.Errorf("три отказа подряд дали %q, ожидался %q", got, statusStale)
	}
	if got := markStale(1, statusBrokenUnreachable); got != statusBrokenUnreachable {
		t.Errorf("единичный отказ уже помечен stale: %q", got)
	}
	// Успешный прогон в stale не превращается ни при каком счётчике.
	if got := markStale(9, statusOnSale); got != statusOnSale {
		t.Errorf("успешный статус затёрт на %q", got)
	}
}
