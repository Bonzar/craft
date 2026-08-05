package main

// Тесты сборки запроса к каналу.
//
// Сеть здесь не трогается: живые формы запроса проверяются отдельным прогоном
// по настоящим кассам, а тут проверяется поведение вокруг них — что канал без
// запроса не выглядит пустой афишей и что частичный отказ горизонта виден.

import (
	"strings"
	"testing"
	"time"
)

// Вид канала, которого нет в диспетчере, обязан дать ошибку.
//
// Пустая афиша вместо ошибки означала бы «у площадки нет сеансов» — то есть
// ненаписанный код выглядел бы как факт о прокате.
func TestFetchChannelUnknownKindFails(t *testing.T) {
	got := fetchChannelDay(newClient(1, 0), "мираж", ChannelParams{pVenue: "vodny"}, time.Now())

	if got.Err == nil {
		t.Fatal("неизвестный вид канала не дал ошибки")
	}
	if !strings.Contains(got.Err.Error(), "мираж") {
		t.Errorf("в ошибке не видно, какой вид не поддержан: %v", got.Err)
	}
	if len(got.Playbill.Showtimes) != 0 {
		t.Error("неизвестный вид отдал сеансы")
	}
}

// Классификатор обязан увидеть отказ неизвестного вида как поломку источника, а
// не как отсутствие фильма.
func TestUnknownKindNeverLooksLikeAbsent(t *testing.T) {
	probe := fetchChannelDay(newClient(1, 0), "люксор", ChannelParams{pVenue: "x"}, time.Now())

	res := classifyProbe(ProbeInput{
		Err:      probe.Err,
		Playbill: probe.Playbill,
		Now:      time.Now(),
	})
	if res.Status == statusAbsent {
		t.Fatal("отсутствие адаптера классифицировано как «фильма нет»")
	}
	if res.Alive {
		t.Error("живость источника доказана без единого запроса")
	}
}

// Пустой день не имеет права перебить код рабочих дней горизонта.
//
// Живой случай: kinoteatr.ru отвечает редиректом на дату, которой у площадки
// нет в расписании. Пока «последний день выигрывал», канал с афишей на неделю и
// одним выходным в конце горизонта уезжал в suspect — то есть выпадал из
// покрытия, будучи полностью рабочим.
func TestMergeStatusKeepsAnsweringDay(t *testing.T) {
	cases := []struct {
		name       string
		have, next int
		want       int
	}{
		{"первый день задаёт код", 0, 200, 200},
		{"пустой день после рабочего код не меняет", 200, 301, 200},
		{"рабочий день после пустого код перебивает", 301, 200, 200},
		{"пустой день после пустого остаётся пустым", 301, 302, 301},
	}
	for _, c := range cases {
		if got := mergeStatus(c.have, c.next); got != c.want {
			t.Errorf("%s: mergeStatus(%d, %d) = %d, ожидалось %d", c.name, c.have, c.next, got, c.want)
		}
	}
}

// А одинокий пустой день живостью не становится: горизонт без единого сеанса —
// это по-прежнему повод присмотреться, а не факт «фильма нет».
func TestRedirectOnlyHorizonIsNotAlive(t *testing.T) {
	res := classifyProbe(ProbeInput{
		HTTPStatus: 301,
		Playbill:   Playbill{Dates: []string{"2026-08-04"}},
		Now:        time.Now(),
	})
	if res.Alive || res.Status == statusAbsent {
		t.Errorf("пустой горизонт засчитан живым каналом: %+v", res)
	}
}

// Горизонт канала, отдающего окно целиком, берётся одним запросом.
func TestChannelWindowWholeCoversKnownKinds(t *testing.T) {
	// Замерено живьём: эти отдают весь свой горизонт за один ответ. У Pushka
	// причина крайняя — параметра даты в запросе нет вовсе, и обход по дню
	// складывал один и тот же ответ сам с собой.
	for _, kind := range []string{kindKaro, kindCinemaStar, kindMoskino, kindPushka} {
		if !channelWindowWhole[kind] {
			t.Errorf("канал %q помечен как однодневный, хотя отдаёт окно целиком", kind)
		}
	}
	// А эти три отвечают строго за одну дату, и день им передаётся запросом.
	for _, kind := range []string{kindKinomax, kindCinemaPark, kind5Zvezd} {
		if channelWindowWhole[kind] {
			t.Errorf("канал %q помечен как отдающий окно, хотя отвечает за один день", kind)
		}
	}
}

// Город берётся у запрошенной площадки, а не у первой в списке приложения.
//
// Живой случай: виджет ЗигЗага знает три площадки — Липецк, Люберцы и Москву,
// и Липецк стоит первым. Пока брался первый, канал уходил за афишей чужого
// города и возвращал ПУСТУЮ афишу при HTTP 200 — то есть площадка выпадала из
// покрытия, выглядя при этом исправной.
func TestKinoplanCityOfPicksRequestedVenue(t *testing.T) {
	app := kinoplanApp{Token: "t"}
	app.Cinemas = append(app.Cinemas,
		struct {
			ID     int `json:"id"`
			CityID int `json:"city_id"`
		}{ID: 120, CityID: 28},
		struct {
			ID     int `json:"id"`
			CityID int `json:"city_id"`
		}{ID: 2465, CityID: 29},
		struct {
			ID     int `json:"id"`
			CityID int `json:"city_id"`
		}{ID: 6552, CityID: 1},
	)

	if got := kinoplanCityOf(app, 6552); got != 1 {
		t.Errorf("город запрошенной площадки = %d, ожидалась Москва (1)", got)
	}
	// Площадки нет в приложении — это промах по идентификатору. Ноль заставляет
	// вызывающего сказать об этом ошибкой, а не подставить чужой город.
	if got := kinoplanCityOf(app, 9999); got != 0 {
		t.Errorf("для отсутствующей площадки вернулся город %d, ожидался 0", got)
	}
}

// Канал, отдающий на каждый день окна один и тот же ответ, не задваивает сеансы.
//
// Живой случай: у Pushka в запросе нет параметра даты, и обход горизонта по дню
// складывал полное расписание площадки само с собой — 140 записей при 70
// реальных временах. Список channelWindowWhole это лечит, но только для
// известных каналов; дедуп обязан удержать инвариант и для неизвестного.
func TestAppendNewShowtimesDropsRepeats(t *testing.T) {
	day := []Showtime{
		{Film: "Одиссея", StartsAt: "2026-08-05T10:00:00+03:00", Hall: "Зал 1"},
		{Film: "Одиссея", StartsAt: "2026-08-05T13:10:00+03:00", Hall: "Зал 1"},
	}

	seen := map[string]bool{}
	var out []Showtime
	// Тот же ответ пришёл дважды — ровно как при обходе двухдневного окна.
	out = appendNewShowtimes(out, day, seen)
	out = appendNewShowtimes(out, day, seen)

	if len(out) != len(day) {
		t.Errorf("сеансов после склейки %d, ожидалось %d — повтор не схлопнулся", len(out), len(day))
	}
}

// Честно разные дни складываются целиком: дедуп не должен съедать данные.
func TestAppendNewShowtimesKeepsDistinctDays(t *testing.T) {
	first := []Showtime{{Film: "Одиссея", StartsAt: "2026-08-05T10:00:00+03:00", Hall: "Зал 1"}}
	second := []Showtime{{Film: "Одиссея", StartsAt: "2026-08-06T10:00:00+03:00", Hall: "Зал 1"}}

	seen := map[string]bool{}
	var out []Showtime
	out = appendNewShowtimes(out, first, seen)
	out = appendNewShowtimes(out, second, seen)

	if len(out) != 2 {
		t.Errorf("сеансов после склейки %d, ожидалось 2 — дедуп съел разные дни", len(out))
	}
}

// Один и тот же фильм в одно время, но в РАЗНЫХ залах — это два сеанса.
//
// Мультиплексы так и работают: параллельные показы в соседних залах. Схлопнуть
// их значило бы потерять половину расписания.
func TestAppendNewShowtimesKeepsParallelHalls(t *testing.T) {
	same := []Showtime{
		{Film: "Одиссея", StartsAt: "2026-08-05T10:00:00+03:00", Hall: "Зал 1"},
		{Film: "Одиссея", StartsAt: "2026-08-05T10:00:00+03:00", Hall: "Зал 2"},
	}

	seen := map[string]bool{}
	out := appendNewShowtimes(nil, same, seen)

	if len(out) != 2 {
		t.Errorf("параллельных сеансов осталось %d, ожидалось 2", len(out))
	}
}

// Фактическое окно источника — от первой даты сеанса до последней.
func TestSourceWindowSpansShowtimes(t *testing.T) {
	pb := Playbill{Showtimes: []Showtime{
		{StartsAt: "2026-08-07T10:00:00+03:00"},
		{StartsAt: "2026-08-05T22:00:00+03:00"},
		{StartsAt: "2026-08-06T13:00:00+03:00"},
	}}

	from, to := sourceWindow(pb)
	if from != "2026-08-05" || to != "2026-08-07" {
		t.Errorf("окно источника %q…%q, ожидалось 2026-08-05…2026-08-07", from, to)
	}
}

// Афиша без сеансов окна не даёт — это отдельный исход, и решает его
// классификатор, а не пустые строки в окне.
func TestSourceWindowEmptyPlaybill(t *testing.T) {
	from, to := sourceWindow(Playbill{})
	if from != "" || to != "" {
		t.Errorf("у пустой афиши появилось окно %q…%q", from, to)
	}
}

// Непокрытыми считаются только КРАЯ запрошенного окна.
//
// Живой случай: КАРО отдаёт 23 даты, сколько ни проси. На горизонте в 28 дней
// пять последних дат остаются без единого ответа источника, и «фильма там нет»
// — утверждение без основания.
func TestUncoveredDatesTakesEdgesOnly(t *testing.T) {
	from := time.Date(2026, 8, 5, 12, 0, 0, 0, time.UTC)

	got := uncoveredDates(from, 5, "2026-08-06", "2026-08-08")

	want := []string{"2026-08-05", "2026-08-09"}
	if len(got) != len(want) {
		t.Fatalf("непокрытых дат %v, ожидалось %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("непокрытые даты %v, ожидалось %v", got, want)
			break
		}
	}
}

// Дырка ВНУТРИ окна источника непокрытой не считается: площадка имеет право не
// работать в этот день, и «фильма нет» там честное.
func TestUncoveredDatesIgnoresHoleInside(t *testing.T) {
	from := time.Date(2026, 8, 5, 12, 0, 0, 0, time.UTC)

	if got := uncoveredDates(from, 3, "2026-08-05", "2026-08-07"); len(got) != 0 {
		t.Errorf("дни внутри окна источника объявлены непокрытыми: %v", got)
	}
}

// Окна нет вовсе — считать нечего: это ветка пустой афиши, у неё свой исход.
func TestUncoveredDatesWithoutWindow(t *testing.T) {
	if got := uncoveredDates(time.Now(), 7, "", ""); got != nil {
		t.Errorf("без окна источника посчитались непокрытые даты: %v", got)
	}
}
