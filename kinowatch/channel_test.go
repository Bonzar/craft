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
	// Замерено живьём: эти три отдают несколько дат за один ответ.
	for _, kind := range []string{kindKaro, kindCinemaStar, kindMoskino} {
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
