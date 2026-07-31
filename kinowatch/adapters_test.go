package main

// Тесты адаптеров первого слоя. Сети нет: разбор проверяется на фикстурах —
// урезанных живых ответах, снятых 31.07.2026 (testdata/*.json).
//
// Ради этого фикстуры и сохранены: без них адаптеры проверялись бы только
// живой сетью, то есть не проверялись бы в CI вовсе.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func readFixture(t *testing.T, name string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatalf("чтение фикстуры %s: %v", name, err)
	}
	return string(data)
}

func TestParseKinomax(t *testing.T) {
	pb, err := parseKinomax(readFixture(t, "kinomax-sessions.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}

	if pb.Cinema == "" {
		t.Error("название площадки потеряно")
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("сеансов ноль — адаптер не разобрал афишу")
	}
	if len(pb.Dates) == 0 {
		t.Error("список дат потерян: глубину горизонта пришлось бы гадать")
	}

	s := pb.Showtimes[0]
	if s.Film == "" || s.StartsAt == "" {
		t.Errorf("сеанс без названия или времени: %+v", s)
	}
	if !strings.HasPrefix(s.StartsAt, "2026-") || !strings.Contains(s.StartsAt, "+03:00") {
		t.Errorf("время собрано неверно: %q — ожидался RFC3339 в московской зоне", s.StartsAt)
	}
	if s.DurationM == 0 {
		t.Error("хронометраж у Киномакса есть, но не разобран — без него не работает уровень каскада про аномальную длительность")
	}
	if s.SourceID == "" {
		t.Error("id сеанса потерян")
	}
}

// Фискальное название — признак серого проката, ради которого адаптер вообще
// смотрит на это поле. Если бы разбор его терял, маскировка ловилась бы только
// косвенными признаками.
func TestParseKinomaxKeepsFiscalName(t *testing.T) {
	pb, err := parseKinomax(readFixture(t, "kinomax-sessions.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}

	fiscal := map[string]string{}
	for _, s := range pb.Showtimes {
		if s.FilmFiscal != "" {
			fiscal[s.Film] = s.FilmFiscal
		}
	}
	if len(fiscal) == 0 {
		t.Fatal("ни одного фискального названия — в фикстуре они есть")
	}
	if got := fiscal["Одиссея"]; got != "Прощание" {
		t.Errorf("у «Одиссеи» фискальное название %q, в живом ответе было «Прощание»", got)
	}

	// Обратная сторона: у лицензионных фильмов поле пустое, и адаптер не должен
	// подставлять туда афишное название.
	for _, s := range pb.Showtimes {
		if s.Film == s.FilmFiscal && s.FilmFiscal != "" {
			t.Errorf("фискальное название продублировало афишное у %q — признак серого проката перестал работать", s.Film)
		}
	}
}

// У Киномакса номера зала нет вовсе — есть только формат. Пустое поле должно
// остаться пустым: подставленный туда формат сломал бы ключ сеанса.
func TestParseKinomaxLeavesHallEmpty(t *testing.T) {
	pb, err := parseKinomax(readFixture(t, "kinomax-sessions.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	for _, s := range pb.Showtimes {
		if s.Hall != "" {
			t.Errorf("в Hall попало %q, хотя источник номера зала не отдаёт", s.Hall)
		}
	}
}

func TestParseKaroSchedule(t *testing.T) {
	pb, err := parseKaroSchedule(
		readFixture(t, "karo-schedule-flat.json"),
		readFixture(t, "karo-schedule-films.json"),
	)
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("сеансов ноль")
	}

	s := pb.Showtimes[0]
	if s.StartsAt == "" {
		t.Error("время сеанса не собрано")
	}
	// Цена у КАРО в копейках: 22000 — это 220 рублей. Без деления в отчёте
	// появились бы билеты по 22 тысячи.
	if s.PriceMin > 5000 {
		t.Errorf("цена %d — похоже, копейки не переведены в рубли", s.PriceMin)
	}
	if s.PriceMin == 0 {
		t.Error("цена потеряна")
	}
}

// Хронометража КАРО не отдаёт ни в сеансах, ни в справочнике. Это не дефект
// разбора, а свойство источника: на этих площадках уровень каскада про
// аномальную длительность неприменим, и уверенность находки обязана быть ниже.
func TestParseKaroHasNoDuration(t *testing.T) {
	pb, err := parseKaroSchedule(
		readFixture(t, "karo-schedule-flat.json"),
		readFixture(t, "karo-schedule-films.json"),
	)
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	for _, s := range pb.Showtimes {
		if s.DurationM != 0 {
			t.Errorf("у КАРО появился хронометраж %d — источник его не отдаёт, значение взялось ниоткуда", s.DurationM)
		}
	}
}

// Сеанс, чьего film_id нет в справочнике, остаётся без названия — и это верное
// поведение. Справочник отдаёт репертуар на сегодня, а сеансы могут быть на
// другую дату; подставить туда чужой фильм значило бы создать ложную находку.
func TestParseKaroLeavesUnknownFilmEmpty(t *testing.T) {
	flat := `{"data":{"items":[{"id":1,"film_id":999999,"format_id":1,"date":"2026-08-02","time":"10:10","standard_price":35000}]}}`
	films := `{"data":{"info":{"name":"КАРО 7 Атриум"},"items":[{"id":16162,"name":"Другой фильм"}],"formats":[1]}}`

	pb, err := parseKaroSchedule(flat, films)
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) != 1 {
		t.Fatalf("сеансов %d, ожидался один", len(pb.Showtimes))
	}
	if got := pb.Showtimes[0].Film; got != "" {
		t.Errorf("неизвестному film_id подставлено название %q", got)
	}
}

func TestParseKinoplan(t *testing.T) {
	pb, err := parseKinoplan(readFixture(t, "kinoplan-playbill.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("сеансов ноль")
	}

	s := pb.Showtimes[0]
	if s.Film == "" {
		t.Error("название фильма потеряно (в ответе оно в поле title, а не name)")
	}
	if s.StartsAt == "" || !strings.Contains(s.StartsAt, "+03:00") {
		t.Errorf("время разобрано как %q — у Kinoplan оно приходит с зоной", s.StartsAt)
	}
	// Единственный источник, отдающий и номер зала, и вилку цены.
	if s.Hall == "" {
		t.Error("номер зала потерян — у Kinoplan он в hall.title")
	}
	if s.PriceMin == 0 || s.PriceMax == 0 {
		t.Errorf("вилка цены не разобрана: %d–%d", s.PriceMin, s.PriceMax)
	}
	if s.PriceMin > 5000 {
		t.Errorf("цена %d — копейки не переведены в рубли", s.PriceMin)
	}
	if s.DurationM == 0 {
		t.Error("хронометраж потерян")
	}
}

// Класс зала (VIP) — про услугу, а не про помещение, поэтому уезжает в Format.
// В Hall остаётся только идентификатор зала, иначе ключ сеанса начнёт различать
// сеансы по классу обслуживания.
func TestParseKinoplanPutsVipIntoFormat(t *testing.T) {
	body := `{"formats":[{"id":1,"title":"2D"}],"releases":[{"title":"Одиссея","duration":187,
	"seances":[{"id":"a1","cinema_id":1,"hall":{"id":1,"title":"3","is_vip":true},
	"start_date":"2026-08-02","start_date_time":"2026-08-02T10:30:00.000+03:00",
	"price":{"min":45000,"max":45000},"is_allowed_online_sale":true,"formats":[{"id":1,"title":"2D"}]}]}]}`

	pb, err := parseKinoplan(body)
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	s := pb.Showtimes[0]
	if s.Hall != "3" {
		t.Errorf("в Hall лежит %q, ожидался только номер зала", s.Hall)
	}
	if !strings.Contains(s.Format, "VIP") {
		t.Errorf("признак VIP потерян: формат %q", s.Format)
	}
	if strings.Contains(s.Hall, "VIP") {
		t.Errorf("класс зала попал в Hall: %q", s.Hall)
	}
}

// Синема-Стар отдаёт всё окно сразу и параметр даты игнорирует — адаптер обязан
// вернуть сеансы всех дат, а фильтрация остаётся на нашей стороне.
func TestParseCinemaStar(t *testing.T) {
	body := `{"data":{"theatre":{"name":"Синема Стар Kvartal West","uid":"kvartal"},
	"schedule":{"dates":["2026-07-31","2026-08-01"],"items":[
	{"film":{"name":"Старый орёл"},"formats":[{"format":"2Д","sessions":[
	{"id":11,"business_date":"2026-07-31","showtime":"2026-07-31 16:05:00","disabled":false,"standard_price":25000},
	{"id":12,"business_date":"2026-08-01","showtime":"2026-08-01 17:50:00","disabled":true,"standard_price":25000}]}]}]}}}`

	pb, err := parseCinemaStar(body)
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) != 2 {
		t.Fatalf("сеансов %d, ожидалось 2 — источник отдаёт всё окно сразу", len(pb.Showtimes))
	}
	if len(pb.Dates) != 2 {
		t.Errorf("список дат разобран как %v", pb.Dates)
	}
	if pb.Showtimes[0].PriceMin != 250 {
		t.Errorf("цена %d — копейки не переведены", pb.Showtimes[0].PriceMin)
	}
	// disabled — сеанс есть в расписании, но купить нельзя. Терять этот признак
	// нельзя: иначе инструмент отрапортует о билетах, которых нет.
	if !pb.Showtimes[0].OnSale {
		t.Error("доступный сеанс помечен недоступным")
	}
	if pb.Showtimes[1].OnSale {
		t.Error("сеанс с disabled=true помечен доступным — это ложная находка билетов")
	}
}

func TestNormalizeShowtime(t *testing.T) {
	cases := []struct{ showtime, date, want string }{
		{"2026-08-02 10:30:00", "2026-08-02", "2026-08-02T10:30:00+03:00"},
		{"10:30", "2026-08-02", "2026-08-02T10:30:00+03:00"},
		{"", "2026-08-02", ""},
		{"мусор", "2026-08-02", ""},
	}
	for _, c := range cases {
		if got := normalizeShowtime(c.showtime, c.date); got != c.want {
			t.Errorf("normalizeShowtime(%q, %q) = %q, want %q", c.showtime, c.date, got, c.want)
		}
	}
}
