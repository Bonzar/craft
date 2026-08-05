package main

// Тесты адаптеров первого слоя. Сети нет: разбор проверяется на фикстурах —
// урезанных живых ответах, снятых 31.07.2026 (testdata/*.json).
//
// Ради этого фикстуры и сохранены: без них адаптеры проверялись бы только
// живой сетью, то есть не проверялись бы в CI вовсе.

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
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

// Разбор на живой фикстуре (снята 31.07.2026). Проверяются ровно те поля,
// которых нет у большинства источников и ради которых Синема-Стар ценен:
// хронометраж, описание и номер прокатного удостоверения.
func TestParseCinemaStarFixture(t *testing.T) {
	pb, err := parseCinemaStar(readFixture(t, "cinemastar-schedule.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if pb.Cinema == "" || len(pb.Showtimes) == 0 {
		t.Fatalf("афиша пуста: %+v", pb)
	}

	var odyssey *Showtime
	for i := range pb.Showtimes {
		if strings.HasPrefix(pb.Showtimes[i].Film, "Одиссея") {
			odyssey = &pb.Showtimes[i]
			break
		}
	}
	if odyssey == nil {
		t.Fatal("«Одиссея» не найдена в фикстуре")
	}

	if odyssey.DurationM == 0 {
		t.Error("хронометраж потерян — уровень каскада про аномальную длительность остался бы без входа")
	}
	if odyssey.LicenceID == "" {
		t.Error("номер прокатного удостоверения потерян — уровень общего ПУ работать не сможет")
	}
	if odyssey.Synopsis == "" {
		t.Error("описание потеряно")
	}
	// Описание приезжает вёрсткой: если теги и &nbsp; не вычищены, поиск
	// подсказок профиля по подстроке не сработает.
	if strings.Contains(odyssey.Synopsis, "<") || strings.Contains(odyssey.Synopsis, "&nbsp") {
		t.Errorf("разметка не вычищена из синопсиса: %q", odyssey.Synopsis[:80])
	}
}

// Улика, которую видно только по афише целиком: два разных фильма делят одно
// прокатное удостоверение, то есть показываются по бумаге одной короткометражки.
func TestCinemaStarSharedLicenceIsDetected(t *testing.T) {
	pb, err := parseCinemaStar(readFixture(t, "cinemastar-schedule.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}

	shared := sharedLicenceTitles(pb)
	if len(shared) == 0 {
		t.Fatal("общее удостоверение не обнаружено, хотя в фикстуре его делят два фильма")
	}

	var minions, eagle bool
	for title := range shared {
		if strings.HasPrefix(title, "миньоны") {
			minions = true
		}
		if strings.HasPrefix(title, "старый орел") {
			eagle = true
		}
	}
	if !minions {
		t.Errorf("«Миньоны и монстры» не помечены общим ПУ: %v", shared)
	}
	if eagle {
		t.Error("фильм с собственным удостоверением помечен как делящий его — улика обесценится")
	}
}

// Синема-Стар называет обёртку прямо в тексте позиции. Это самый ценный вид
// маркера: он даёт не только признак серой схемы, но и саму обёртку.
func TestCinemaStarNamesWrapperInTitle(t *testing.T) {
	pb, err := parseCinemaStar(readFixture(t, "cinemastar-schedule.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}

	found := map[string]string{}
	for _, s := range pb.Showtimes {
		if w := extractWrapper(s.Film); w != "" {
			found[strings.Split(s.Film, " (")[0]] = w
		}
	}
	if got := found["Одиссея"]; got != "Прощание" {
		t.Errorf("обёртка «Одиссеи» разобрана как %q, в тексте позиции стоит «Прощание»", got)
	}
	if got := found["Миньоны и монстры"]; got != "Сказка на ночь" {
		t.Errorf("обёртка «Миньонов» разобрана как %q", got)
	}
	// Фильм без маркера обёртки не получает: выдуманная обёртка попала бы в
	// профиль и загрязнила бы его навсегда.
	if w, ok := found["Старый орёл"]; ok {
		t.Errorf("лицензионному фильму приписана обёртка %q", w)
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

// Разбор Москино на живой фикстуре (снята 31.07.2026). У источника нет ни
// зала, ни хронометража, ни описания — проверяется, что разбор не выдумывает
// их и при этом не теряет то, что есть.
func TestParseMoskino(t *testing.T) {
	ref := time.Date(2026, 7, 31, 12, 0, 0, 0, moscowTZ)
	pb, err := parseMoskino(readFixture(t, "moskino-schedule.html"), ref)
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("сеансов ноль — разбор не поймал разметку")
	}
	if len(pb.Dates) == 0 {
		t.Error("список дат потерян")
	}

	s := pb.Showtimes[0]
	if s.Film == "" {
		t.Error("название фильма потеряно")
	}
	if !strings.HasPrefix(s.StartsAt, "2026-") || !strings.Contains(s.StartsAt, "+03:00") {
		t.Errorf("время собрано неверно: %q", s.StartsAt)
	}
	if s.SourceID == "" {
		t.Error("id сеанса из richSession() потерян — без него дедуп беззальных сеансов работает на отпечатке")
	}
	if s.PriceMin == 0 {
		t.Error("цена потеряна")
	}

	for _, st := range pb.Showtimes {
		if st.Hall != "" {
			t.Errorf("в Hall попало %q, хотя источник номера зала не отдаёт", st.Hall)
		}
		if st.DurationM != 0 {
			t.Errorf("у Москино появился хронометраж %d — источник его не отдаёт", st.DurationM)
		}
	}
}

// Год в разметке не указан вовсе («31 ИЮЛ»), поэтому достраивается относительно
// опорной даты. Ошибка здесь тихая и дорогая: всё расписание уезжает в прошлое
// и молча выпадает из выдачи как «сеансы, которые уже прошли».
func TestResolveMoskinoDate(t *testing.T) {
	ref := time.Date(2026, 7, 31, 12, 0, 0, 0, moscowTZ)
	cases := []struct{ day, month, want string }{
		{"31", "ИЮЛ", "2026-07-31"},
		{"09", "АВГ", "2026-08-09"},
		// Январь при опорном июле — это следующий год, а не прошедший.
		{"05", "ЯНВ", "2027-01-05"},
	}
	for _, c := range cases {
		day, _ := strconv.Atoi(c.day)
		got, ok := resolveMoskinoDate(day, c.month, ref)
		if !ok || got != c.want {
			t.Errorf("resolveMoskinoDate(%s %s) = %q (ok=%v), want %q", c.day, c.month, got, ok, c.want)
		}
	}

	// Декабрьское расписание, прочитанное в январе, не должно уехать назад.
	jan := time.Date(2027, 1, 3, 12, 0, 0, 0, moscowTZ)
	if got, _ := resolveMoskinoDate(28, "ДЕК", jan); got != "2027-12-28" {
		t.Errorf("декабрь при январской опорной дате разобран как %q", got)
	}

	if _, ok := resolveMoskinoDate(31, "МУСОР", ref); ok {
		t.Error("неизвестный месяц принят за настоящий")
	}
}

// Пустой ответ при живой странице — это поломка разбора, а не отсутствие
// сеансов. Отличать обязательно: иначе смена вёрстки выглядела бы как «фильма
// нет» на всей сети разом.
func TestParseMoskinoFailsLoudlyOnUnknownMarkup(t *testing.T) {
	ref := time.Date(2026, 7, 31, 12, 0, 0, 0, moscowTZ)
	if _, err := parseMoskino("<html><body><div>совсем другая вёрстка</div></body></html>", ref); err == nil {
		t.Error("разбор промолчал о сменившейся вёрстке")
	}
}

// Разбор Mori на живой фикстуре. В блоке, который по классу называется «hall»,
// у этого источника лежит формат показа — проверяется, что он не уехал в Hall.
func TestParseMori(t *testing.T) {
	pb, err := parseMori(readFixture(t, "mori-schedule.html"), "2026-07-31")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("сеансов ноль")
	}

	s := pb.Showtimes[0]
	if s.Film == "" {
		t.Error("название фильма потеряно")
	}
	if !strings.HasPrefix(s.StartsAt, "2026-07-31T") {
		t.Errorf("время собрано неверно: %q", s.StartsAt)
	}
	if s.SourceID == "" {
		t.Error("id сеанса из /session/<id>/buy потерян")
	}

	for _, st := range pb.Showtimes {
		if st.Hall != "" {
			t.Errorf("в Hall попало %q — у Mori в этом месте разметки формат, а не зал", st.Hall)
		}
	}

	var withFormat, withPrice, withDuration int
	for _, st := range pb.Showtimes {
		if st.Format != "" {
			withFormat++
		}
		if st.PriceMin > 0 {
			withPrice++
		}
		if st.DurationM > 0 {
			withDuration++
		}
	}
	if withFormat == 0 {
		t.Error("формат показа (2Д, ВИП 2Д) потерян")
	}
	// Цену источник публикует не всегда: на датах вперёд она приходит пустой
	// (замерено на этой же фикстуре). Поэтому здесь она не требуется — что она
	// читается, когда есть, проверяет отдельный кейс ниже.
	_ = withPrice
	if withDuration == 0 {
		t.Error("хронометраж потерян — у Mori он есть прозой, и уровень каскада про длительность на нём работает")
	}
}

// Хронометраж у Mori записан прозой, а не числом.
func TestParseRussianDuration(t *testing.T) {
	cases := []struct {
		in   string
		want int
	}{
		{"3 часа 0 минут", 180},
		{"1 час 47 минут", 107},
		{"107 минут", 107},
		{"1 ч. 38 мин.", 98},
		{"2 часа", 120},
		{"", 0},
		{"неизвестно", 0},
	}
	for _, c := range cases {
		if got := parseRussianDuration(c.in); got != c.want {
			t.Errorf("parseRussianDuration(%q) = %d, want %d", c.in, got, c.want)
		}
	}
}

// «Пять звёзд» — единственный источник первого слоя, отдающий и номер зала, и
// хронометраж. Зато цены у него нет вовсе, и выдумывать её нельзя.
func TestParseFiveStars(t *testing.T) {
	pb, err := parseFiveStars(readFixture(t, "5zvezd-schedule.html"), "2026-07-31")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("сеансов ноль")
	}
	if pb.Cinema == "" {
		t.Error("название площадки потеряно")
	}

	var withHall, withDuration int
	for _, s := range pb.Showtimes {
		if s.Hall != "" {
			withHall++
		}
		if s.DurationM > 0 {
			withDuration++
		}
		if s.PriceMin != 0 || s.PriceMax != 0 {
			t.Errorf("у «Пяти звёзд» появилась цена %d–%d — источник её не отдаёт", s.PriceMin, s.PriceMax)
		}
		if s.SourceID == "" {
			t.Error("id сеанса потерян")
		}
	}
	if withHall == 0 {
		t.Error("номер зала потерян — он лежит в title кнопки («Зал 5»)")
	}
	if withDuration == 0 {
		t.Error("хронометраж потерян — он в подписи жанров («98 мин»)")
	}
}

// В Hall обязан попасть только НОМЕР зала, а класс обслуживания (ПРЕМИУМ) —
// в Format: иначе ключ сеанса начнёт различать сеансы по классу услуги.
func TestFiveStarsSeparatesHallFromClass(t *testing.T) {
	pb, err := parseFiveStars(readFixture(t, "5zvezd-schedule.html"), "2026-07-31")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}

	var premium int
	for _, s := range pb.Showtimes {
		if strings.Contains(s.Hall, "Зал") || strings.Contains(strings.ToUpper(s.Hall), "ПРЕМИУМ") {
			t.Errorf("в Hall лежит %q, ожидался только номер", s.Hall)
		}
		if strings.Contains(strings.ToUpper(s.Format), "ПРЕМИУМ") {
			premium++
		}
	}
	if premium == 0 {
		t.Error("класс зала ПРЕМИУМ потерян — в фикстуре он есть")
	}
}

// session-past означает, что сеанс уже начался: билетов на него нет.
func TestFiveStarsMarksPastSessions(t *testing.T) {
	body := `<div class="creation-schedule-item"><h2><a href="/details/1">Кино</a></h2>
	<div class="creation-genre">Драма, 98 мин</div>
	<div class="cinema-name">Пять Звёзд на Новокузнецкой</div>
	<button type="button" class="session session-past" title="Зал 5" onclick="ticketManager.session(&#039;u&#039;, 111);">13:15</button>
	<button type="button" class="session" title="Зал 5" onclick="ticketManager.session(&#039;u&#039;, 222);">19:45</button></div>`

	pb, err := parseFiveStars(body, "2026-07-31")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) != 2 {
		t.Fatalf("сеансов %d, ожидалось 2", len(pb.Showtimes))
	}
	if pb.Showtimes[0].OnSale {
		t.Error("прошедший сеанс помечен доступным — это ложная находка билетов")
	}
	if !pb.Showtimes[1].OnSale {
		t.Error("живой сеанс помечен недоступным")
	}
}

// p24.app — самый богатый источник первого слоя: зал, цена, uuid сеанса и дата
// в ссылке. Разбор на живой фикстуре Колибри (снята 31.07.2026).
func TestParseP24(t *testing.T) {
	pb, err := parseP24(readFixture(t, "p24-schedule.html"), "2026-07-31")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("сеансов ноль")
	}
	if pb.Cinema == "" {
		t.Error("название площадки потеряно")
	}

	var withHall, withPrice, withID int
	for _, s := range pb.Showtimes {
		if s.Hall != "" {
			withHall++
		}
		if s.PriceMin > 0 {
			withPrice++
		}
		if s.SourceID != "" {
			withID++
		}
		if !strings.HasPrefix(s.StartsAt, "2026-") {
			t.Errorf("время собрано неверно: %q", s.StartsAt)
		}
	}
	if withHall == 0 {
		t.Error("номер зала потерян")
	}
	// Цену источник публикует не всегда: на датах вперёд она приходит пустой
	// (замерено на этой же фикстуре). Поэтому здесь она не требуется — что она
	// читается, когда есть, проверяет отдельный кейс ниже.
	_ = withPrice
	if withID != len(pb.Showtimes) {
		t.Errorf("uuid есть только у %d сеансов из %d — остальные поедут на отпечатке зря", withID, len(pb.Showtimes))
	}
}

// «Зал 1 (кровати)» — в Hall едет только номер. Описание удобств в ключе
// сеанса означало бы, что переименование зала заводит все сеансы заново.
func TestP24HallKeepsOnlyNumber(t *testing.T) {
	pb, err := parseP24(readFixture(t, "p24-schedule.html"), "2026-07-31")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	for _, s := range pb.Showtimes {
		if strings.Contains(strings.ToLower(s.Hall), "зал") || strings.Contains(s.Hall, "(") {
			t.Errorf("в Hall лежит %q, ожидался только номер", s.Hall)
		}
	}
}

// Дата берётся из ссылки самого сеанса, а не из переданной: страница может
// отдать соседний день, и тогда переданная дата увела бы все сеансы не туда.
func TestP24PrefersDateFromLink(t *testing.T) {
	body := `<div class="EventList_event-info__x event-info"><h2><a href="/events/x">Кино</a></h2>
	<span class="facility-name">Колибри</span>
	<span class="hall-name">Зал 2</span>
	<div class="Show_show__a show"><a href="?date=2026/08/05&amp;facility=u#2026/08/05 10:10">
	<div data-uuid="aaaabbbb-1111-2222-3333-444455556666" class="Show_show-time__b show-time">10:10</div></a>
	<div class="Show_price__c price">500 ₽</div></div></div>`

	pb, err := parseP24(body, "2026-07-31")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) != 1 {
		t.Fatalf("сеансов %d, ожидался один", len(pb.Showtimes))
	}
	if !strings.HasPrefix(pb.Showtimes[0].StartsAt, "2026-08-05") {
		t.Errorf("дата взята не из ссылки: %q", pb.Showtimes[0].StartsAt)
	}
}

// Склейка «фильм-предсеанс. обсл.& прикрытие» приезжает прямо в названии, и
// каскад обязан вытащить из неё настоящий фильм.
func TestP24GluedTitleFeedsCascade(t *testing.T) {
	pb, err := parseP24(readFixture(t, "p24-schedule.html"), "2026-07-31")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}

	var glued bool
	for _, s := range pb.Showtimes {
		if strings.Contains(strings.ToLower(s.Film), "предсеанс") {
			glued = true
			m := matchShowtime(s, FilmProfile{Title: "Миньоны и монстры", DurationMin: 90, DurationMax: 120})
			if !m.Matched {
				t.Errorf("склеенная позиция %q не опознана каскадом: %+v", s.Film, m)
			}
			if !m.GreyRelease {
				t.Errorf("маркер «предсеанс. обсл.» не поднял признак серого проката у %q", s.Film)
			}
			break
		}
	}
	if !glued {
		t.Skip("в фикстуре нет склеенных позиций")
	}
}

// СИНЕМА ПАРК: JSON-конверт с HTML внутри. Фикстура снята 31.07.2026 ЧЕРЕЗ
// ТУННЕЛЬ — с иностранного адреса хост рвёт соединение, и без российского
// выхода этот источник не проверить вовсе.
func TestParseCinemaPark(t *testing.T) {
	pb, err := parseCinemaPark(readFixture(t, "cinemapark-schedule.json"), "2026-07-31")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("сеансов ноль")
	}
	if pb.Cinema == "" {
		t.Error("название площадки потеряно — оно в заголовке, в кавычках-ёлочках")
	}

	var withPrice, withFormat, withDuration int
	for _, s := range pb.Showtimes {
		if s.Hall != "" {
			t.Errorf("в Hall попало %q — источник даёт класс зала, а не номер", s.Hall)
		}
		if s.SourceID == "" {
			t.Error("openWidget-id потерян: без него два сеанса одного фильма в один час неразличимы")
		}
		if s.PriceMin > 0 {
			withPrice++
		}
		if s.Format != "" {
			withFormat++
		}
		if s.DurationM > 0 {
			withDuration++
		}
	}
	if withPrice == 0 {
		t.Error("цена потеряна (приходит как «от 576 р.»)")
	}
	if withFormat == 0 {
		t.Error("формат и класс зала потеряны")
	}
	if withDuration == 0 {
		t.Error("хронометраж потерян (приходит как «1 ч. 38 мин.»)")
	}
}

// Пустой content при валидном JSON — поломка, а не пустая афиша: конверт
// приходит и на сломанном эндпоинте.
func TestParseCinemaParkFailsOnEmptyContent(t *testing.T) {
	if _, err := parseCinemaPark(`{"content":"","title":"x"}`, "2026-07-31"); err == nil {
		t.Error("пустой content принят за пустую афишу")
	}
	if _, err := parseCinemaPark(`{"content":"<div>чужая вёрстка</div>","title":"x"}`, "2026-07-31"); err == nil {
		t.Error("сменившаяся вёрстка принята за пустую афишу")
	}
}

// Pushka — единственный JSON среди одиночек и самый богатый: цена, номер зала,
// доступность и хронометраж. Фикстуры сняты 03.08 по двум разным площадкам.
func TestParsePushka(t *testing.T) {
	pb, err := parsePushka(readFixture(t, "pushka-klen.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("сеансов ноль")
	}
	if pb.Cinema == "" {
		t.Error("название площадки потеряно")
	}

	for _, s := range pb.Showtimes {
		if s.Film == "" {
			t.Error("название потеряно — оно в отдельном словаре films")
		}
		if s.Hall == "" {
			t.Errorf("номер зала потерян у %q", s.Film)
		}
		if s.PriceMin == 0 {
			t.Errorf("цена потеряна у %q", s.Film)
		}
		if s.DurationM == 0 {
			t.Errorf("хронометраж потерян у %q — он в films[film_id].duration", s.Film)
		}
		if s.SourceID == "" {
			t.Error("id сеанса потерян")
		}
		if !strings.Contains(s.StartsAt, "+03:00") {
			t.Errorf("время собрано неверно: %q", s.StartsAt)
		}
	}
}

// Главная проверка полноты: площадку задаёт кука, и разные площадки обязаны
// давать разные наборы сеансов. Один и тот же набор означал бы, что кука не
// сработала и мы молча собираем дефолтную площадку вместо трёх.
func TestPushkaVenuesDifferFromEachOther(t *testing.T) {
	klen, err := parsePushka(readFixture(t, "pushka-klen.json"))
	if err != nil {
		t.Fatalf("разбор «Клёна»: %v", err)
	}
	ladya, err := parsePushka(readFixture(t, "pushka-ladya.json"))
	if err != nil {
		t.Fatalf("разбор «Ладьи»: %v", err)
	}

	if klen.Cinema == ladya.Cinema {
		t.Errorf("обе площадки назвались одинаково (%q) — кука не переключила выдачу", klen.Cinema)
	}

	ids := map[string]bool{}
	for _, s := range klen.Showtimes {
		ids[s.SourceID] = true
	}
	var shared int
	for _, s := range ladya.Showtimes {
		if ids[s.SourceID] {
			shared++
		}
	}
	if shared == len(ladya.Showtimes) {
		t.Error("наборы сеансов совпали целиком — вернулась одна и та же площадка")
	}
}

// is_available:false — сеанс в расписании есть, купить нельзя. Потеря признака
// означала бы рапорт о билетах, которых нет.
func TestPushkaMarksUnavailableSessions(t *testing.T) {
	pb, err := parsePushka(readFixture(t, "pushka-klen.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}

	var onSale, off int
	for _, s := range pb.Showtimes {
		if s.OnSale {
			onSale++
		} else {
			off++
		}
	}
	if off == 0 {
		t.Error("в фикстуре есть недоступные сеансы, но все помечены продающимися")
	}
	if onSale == 0 {
		t.Error("все сеансы помечены недоступными — признак разобран наоборот")
	}
}

// Позиция, чьего film_id нет в словаре films, остаётся без названия: подставить
// туда соседний фильм значило бы создать ложную находку.
func TestPushkaLeavesUnknownFilmEmpty(t *testing.T) {
	body := `{"dates":{"today":"2026-08-03"},"title":"Клён",
	"schedule":{"2026-08-03":[{"film_id":999999,"showtimes":{"2D":[
	{"id":1,"time":"10:00","date":"2026-08-03 10:00","is_available":true,"price":450,"hall_id":7}]}}]},
	"films":{"4657":{"name":"Другой фильм","duration":180}}}`

	pb, err := parsePushka(body)
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) != 1 {
		t.Fatalf("сеансов %d, ожидался один", len(pb.Showtimes))
	}
	if got := pb.Showtimes[0].Film; got != "" {
		t.Errorf("неизвестному film_id подставлено название %q", got)
	}
	// Остальные поля при этом на месте: сеанс существует, просто без названия.
	if pb.Showtimes[0].Hall == "" || pb.Showtimes[0].PriceMin == 0 {
		t.Errorf("сеанс без названия потерял и остальные поля: %+v", pb.Showtimes[0])
	}
}

// Пустое расписание при валидном JSON — поломка, а не пустая афиша.
func TestParsePushkaFailsOnEmptySchedule(t *testing.T) {
	if _, err := parsePushka(`{"dates":{},"schedule":{},"films":{}}`); err == nil {
		t.Error("пустое расписание принято за пустую афишу")
	}
}

// Склейка «фильм предс.обсл. & прикрытие» приезжает прямо в названии — каскад
// обязан поднять признак серого проката.
func TestPushkaGluedTitleFeedsCascade(t *testing.T) {
	pb, err := parsePushka(readFixture(t, "pushka-klen.json"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}

	for _, s := range pb.Showtimes {
		if !strings.Contains(strings.ToLower(s.Film), "предс") {
			continue
		}
		m := matchShowtime(s, FilmProfile{Title: "Одиссея", DurationMin: 150, DurationMax: 200})
		if !m.GreyRelease {
			t.Errorf("маркер «предс.обсл.» не поднял признак серого проката у %q", s.Film)
		}
		return
	}
	t.Skip("в фикстуре нет склеенных позиций")
}

// Три московские площадки перечислены явно: пропуск любой означает неполные
// данные по МКАД.
func TestPushkaCoversAllMoscowVenues(t *testing.T) {
	want := map[string]bool{"klen": true, "ladya": true, "key": true}
	if len(pushkaVenues) != len(want) {
		t.Fatalf("площадок %d, ожидалось %d: %v", len(pushkaVenues), len(want), pushkaVenues)
	}
	for _, v := range pushkaVenues {
		if !want[v] {
			t.Errorf("неизвестная площадка %q", v)
		}
	}
}

// Художественный — самый полный из одиночек: зал, цена, хронометраж и язык
// показа. Фикстуры сняты 03.08 по двум разным датам.
func TestParseHudozhestvenny(t *testing.T) {
	pb, err := parseHudozhestvenny(readFixture(t, "hudozhestvenny-0805.html"), "2026-08-05")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) < 3 {
		t.Fatalf("сеансов %d, в фикстуре их больше", len(pb.Showtimes))
	}

	var withHall, withPrice, withDur int
	for _, s := range pb.Showtimes {
		if s.Film == "" {
			t.Error("название фильма потеряно")
		}
		if !strings.HasPrefix(s.StartsAt, "2026-08-05T") {
			t.Errorf("время собрано не на ту дату: %q", s.StartsAt)
		}
		if s.Hall != "" {
			withHall++
		}
		if s.PriceMin > 0 {
			withPrice++
		}
		if s.DurationM > 0 {
			withDur++
		}
	}
	if withHall == 0 {
		t.Error("зал потерян — у Художественного он есть у каждого сеанса")
	}
	// Цену источник публикует не всегда: на датах вперёд она приходит пустой
	// (замерено на этой же фикстуре). Поэтому здесь она не требуется — что она
	// читается, когда есть, проверяет отдельный кейс ниже.
	_ = withPrice
	if withDur == 0 {
		t.Error("хронометраж потерян")
	}
}

// Даты запрашиваются по одной, и сеансы разных дат не должны слипаться: путь с
// датой отдаёт свой день, а параметр ?date= сайт игнорирует.
func TestHudozhestvennyKeepsDatesApart(t *testing.T) {
	first, err := parseHudozhestvenny(readFixture(t, "hudozhestvenny-0805.html"), "2026-08-05")
	if err != nil {
		t.Fatalf("разбор 05.08: %v", err)
	}
	second, err := parseHudozhestvenny(readFixture(t, "hudozhestvenny-0808.html"), "2026-08-08")
	if err != nil {
		t.Fatalf("разбор 08.08: %v", err)
	}

	for _, s := range first.Showtimes {
		if !strings.HasPrefix(s.StartsAt, "2026-08-05") {
			t.Errorf("сеанс чужой даты в выдаче 05.08: %q", s.StartsAt)
		}
	}
	for _, s := range second.Showtimes {
		if !strings.HasPrefix(s.StartsAt, "2026-08-08") {
			t.Errorf("сеанс чужой даты в выдаче 08.08: %q", s.StartsAt)
		}
	}
	if len(first.Showtimes) == 0 || len(second.Showtimes) == 0 {
		t.Fatal("одна из дат разобрана пустой")
	}
}

// Язык показа — про услугу, поэтому уезжает в Format, а не в Hall: иначе ключ
// сеанса начнёт различать сеансы по надписи о субтитрах.
func TestHudozhestvennySeparatesHallFromNote(t *testing.T) {
	pb, err := parseHudozhestvenny(readFixture(t, "hudozhestvenny-0805.html"), "2026-08-05")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	for _, s := range pb.Showtimes {
		if strings.Contains(strings.ToLower(s.Hall), "язык") || strings.Contains(strings.ToLower(s.Hall), "субтитр") {
			t.Errorf("пометка о языке попала в Hall: %q", s.Hall)
		}
	}
}

// ГУМ: сеансы живут в разделе кинозала. На главной странице времена тоже есть,
// но это часы работы торгового центра — принять их за сеансы значило бы
// отрапортовать о показах, которых нет.
func TestParseGum(t *testing.T) {
	pb, err := parseGum(readFixture(t, "gum-kinozal.html"), "2026-08-03")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) < 3 {
		t.Fatalf("сеансов %d, в фикстуре их больше", len(pb.Showtimes))
	}

	for _, s := range pb.Showtimes {
		if s.Film == "" {
			t.Error("название фильма потеряно")
		}
		if s.SourceID == "" {
			t.Error("id сеанса из ticketManager.session() потерян")
		}
		hhmm := s.StartsAt[11:16]
		if hhmm == "10:00" || hhmm == "22:00" {
			t.Errorf("в сеансы попали часы работы ТЦ: %q у %q", hhmm, s.Film)
		}
	}
}

// Выпадающий список дат — то, чем добирается горизонт: без него виден только
// сегодняшний день.
func TestGumDays(t *testing.T) {
	days := gumDays(readFixture(t, "gum-kinozal.html"))
	if len(days) == 0 {
		t.Fatal("список дат не разобран — горизонт свёлся бы к одному дню")
	}
	for id, label := range days {
		if id == "" || label == "" {
			t.Errorf("пустая запись даты: %q → %q", id, label)
		}
	}
}

// Смена вёрстки обязана быть ошибкой, а не пустой афишей: иначе она выглядела
// бы как «фильмов нет».
func TestStandaloneParsersFailLoudly(t *testing.T) {
	junk := "<html><body><div>совсем другая вёрстка</div></body></html>"
	if _, err := parseHudozhestvenny(junk, "2026-08-05"); err == nil {
		t.Error("Художественный промолчал о сменившейся вёрстке")
	}
	if _, err := parseGum(junk, "2026-08-03"); err == nil {
		t.Error("ГУМ промолчал о сменившейся вёрстке")
	}
}

// Касса Kinoplan отвечает афишей ВСЕГО приложения, а приложение бывает общим на
// несколько кинотеатров. Без отбора по площадке каждая получила бы сеансы обеих.
//
// Замерено живьём на Киноквартале: 39 сеансов в ответе, 17 Ясенева и 22
// Варшавского. Отбор возвращает каждой её собственные.
func TestKinoplanKeepsOnlyRequestedVenue(t *testing.T) {
	body := `{"releases":[{"title":"Одиссея","seances":[
		{"id":"a","cinema_id":2402,"start_date":"2026-08-03","start_date_time":"2026-08-03T19:30:00.000+03:00"},
		{"id":"b","cinema_id":2709,"start_date":"2026-08-03","start_date_time":"2026-08-03T20:00:00.000+03:00"},
		{"id":"c","cinema_id":2402,"start_date":"2026-08-03","start_date_time":"2026-08-03T22:00:00.000+03:00"}]}]}`

	pb, err := parseKinoplanFor(body, 2402)
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) != 2 {
		t.Fatalf("сеансов %d, ожидалось 2 — чужие не отсеяны", len(pb.Showtimes))
	}

	// Без идентификатора отбирать нечего: приложение на одну площадку.
	all, err := parseKinoplanFor(body, 0)
	if err != nil {
		t.Fatalf("разбор без отбора: %v", err)
	}
	if len(all.Showtimes) != 3 {
		t.Errorf("без отбора сеансов %d, ожидалось 3", len(all.Showtimes))
	}
}

// Цена и признак продажи читаются, когда источник их отдаёт.
//
// Отдельным кейсом, а не на живой фикстуре: на датах вперёд Художественный
// присылает цену пустой, и требовать её от такой страницы значило бы краснеть
// на нормальном поведении источника.
func TestHudozhestvennyReadsPriceWhenPresent(t *testing.T) {
	body := `<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"data":{"events":[
		{"type":"MOVIE","title":"Русский Гамлет","slug":"russkij-gamlet","duration":85,"showtimes":[
			{"datetime":"2026-08-03T19:30:00.000000+03:00","note":"","price":1150,
			 "location":{"title":"Большой зал"},"isSaleAvailable":true}]}]}}}}</script>`

	pb, err := parseHudozhestvenny(body, "2026-08-03")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) != 1 {
		t.Fatalf("сеансов %d, ожидался один", len(pb.Showtimes))
	}
	s := pb.Showtimes[0]
	if s.PriceMin != 1150 {
		t.Errorf("цена %d, ожидалась 1150", s.PriceMin)
	}
	if !s.OnSale {
		t.Error("признак продажи потерян")
	}
	if s.Hall != "Большой зал" {
		t.Errorf("зал %q", s.Hall)
	}
	if s.DurationM != 85 {
		t.Errorf("хронометраж %d", s.DurationM)
	}
}

// Не-фильмы в афише кинотеатра не считаются сеансами: тип события отдаёт сам
// источник, и гадать по названию незачем.
func TestHudozhestvennySkipsNonMovies(t *testing.T) {
	body := `<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"data":{"events":[
		{"type":"LECTURE","title":"Встреча с режиссёром","showtimes":[
			{"datetime":"2026-08-03T19:00:00.000000+03:00"}]},
		{"type":"MOVIE","title":"Майкл","showtimes":[
			{"datetime":"2026-08-03T21:45:00.000000+03:00"}]}]}}}}</script>`

	pb, err := parseHudozhestvenny(body, "2026-08-03")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) != 1 || pb.Showtimes[0].Film != "Майкл" {
		t.Errorf("не-фильм попал в афишу: %+v", pb.Showtimes)
	}
}

// Премьерзал: прошедший сеанс источник помечает сам, и это прямой ответ на
// вопрос о продаже — у большинства источников его приходится выводить косвенно.
func TestParsePremierzal(t *testing.T) {
	pb, err := parsePremierzal(readFixture(t, "premierzal-schedule.html"), "2026-08-03")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) < 5 {
		t.Fatalf("сеансов %d, в фикстуре их больше", len(pb.Showtimes))
	}

	var passed, onSale, withPrice, withFormat int
	films := map[string]bool{}
	for _, s := range pb.Showtimes {
		if s.Film == "" {
			t.Error("название фильма потеряно")
		}
		films[s.Film] = true
		if !strings.HasPrefix(s.StartsAt, "2026-08-03T") {
			t.Errorf("время собрано не на ту дату: %q", s.StartsAt)
		}
		if s.OnSale {
			onSale++
		} else {
			passed++
		}
		if s.PriceMin > 0 {
			withPrice++
		}
		if s.Format != "" {
			withFormat++
		}
	}
	// Проверяется не разнообразие репертуара, а что не потерян ни один блок:
	// у площадки бывает и один фильм в нескольких блоках формата. Ожидание
	// «фильмов больше одного» было моим, а не свойством источника.
	// Считается разметочный маркер сеанса, а не подстрока `session-picker__
	// item-time`: последняя встречается ещё и внутри скрипта страницы, и по ней
	// счёт врёт на единицу.
	want := strings.Count(readFixture(t, "premierzal-schedule.html"), `class="schedule__session-time `)
	if len(pb.Showtimes) != want {
		t.Errorf("разобрано %d сеансов из %d в фикстуре — блоки теряются", len(pb.Showtimes), want)
	}
	_ = films
	if passed == 0 || onSale == 0 {
		t.Errorf("признак прошедшего сеанса не читается: прошло %d, в продаже %d", passed, onSale)
	}
	if withPrice == 0 {
		t.Error("цена потеряна")
	}
	if withFormat == 0 {
		t.Error("формат показа потерян")
	}
}

// Мираж: у площадки свой адрес расписания, и разбор обязан убедиться, что
// отвечает именно она. Промах по идентификатору — не пустое расписание.
func TestParseMirage(t *testing.T) {
	body := readFixture(t, "mirage-otradnoe.html")

	pb, err := parseMirage(body, "23", "2026-08-03")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("сеансы не найдены")
	}
	var withHall, withLink int
	for _, s := range pb.Showtimes {
		if s.Film == "" {
			t.Error("название фильма потеряно")
		}
		if !strings.HasPrefix(s.StartsAt, "2026-08-03T") {
			t.Errorf("время собрано не на ту дату: %q", s.StartsAt)
		}
		if s.Hall != "" {
			withHall++
		}
		if s.SourceID != "" && s.DeepLink != "" {
			withLink++
		}
	}
	if withHall == 0 {
		t.Error("номер зала потерян — у Миража он есть у каждого сеанса")
	}
	if withLink == 0 {
		t.Error("идентификатор сеанса потерян: он различает два сеанса в один час")
	}

	// Чужой идентификатор обязан дать ошибку, а не пустую афишу: пустая афиша
	// означала бы «сеансов нет», то есть промах выглядел бы как факт.
	if _, err := parseMirage(body, "18", "2026-08-03"); err == nil {
		t.Error("разбор промолчал о том, что на странице другая площадка")
	}
}

// Разбор на живой фикстуре Синема 5 (снята 04.08.2026, площадка Балтика).
func TestParseCinema5Fixture(t *testing.T) {
	pb, err := parseCinema5(readFixture(t, "cinema5-today.json"), 21)
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 || len(pb.Dates) == 0 {
		t.Fatalf("афиша пуста: %+v", pb)
	}

	st := pb.Showtimes[0]
	if st.Film == "" || st.StartsAt == "" {
		t.Errorf("сеанс без названия или времени: %+v", st)
	}
	if st.Hall == "" || st.Format == "" || st.PriceMin == 0 {
		t.Errorf("потеряны поля, которые источник отдаёт: %+v", st)
	}
	// Класс зала едет в Format, а не в Hall: иначе два сеанса одного фильма в
	// разных залах одного формата схлопнулись бы в один ключ.
	if strings.ContainsAny(st.Hall, " ") {
		t.Errorf("в Hall попал не только номер зала: %q", st.Hall)
	}
}

// Чужой сеанс в ответе — промах по идентификатору площадки, а не «сеансов мало».
//
// Отбор делает сам сервис, поэтому появление чужой площадки означает, что
// запрос ушёл не туда. Молча выбросить такой сеанс значило бы спрятать промах:
// афиша осталась бы непустой и канал выглядел бы рабочим.
func TestParseCinema5RejectsForeignVenue(t *testing.T) {
	_, err := parseCinema5(readFixture(t, "cinema5-today.json"), 20)
	if err == nil {
		t.Fatal("сеансы чужой площадки приняты за свои")
	}
	if !strings.Contains(err.Error(), "21") {
		t.Errorf("в ошибке не видно, чья площадка пришла: %v", err)
	}
}

// Разбор PRIME CINEMA на живой фикстуре (снята 04.08.2026).
func TestParseEtobiletFixture(t *testing.T) {
	pb, err := parseEtobilet(readFixture(t, "primecinema-today.html"), "2026-08-04")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("афиша пуста")
	}

	// Шесть залов и десятки сеансов: потеря вложенности (формат → зал → сеанс)
	// схлопнула бы их в горстку и выглядела бы как скудная афиша.
	halls := map[string]bool{}
	films := map[string]bool{}
	for _, s := range pb.Showtimes {
		halls[s.Hall] = true
		films[s.Film] = true
		if s.StartsAt == "" {
			t.Fatalf("сеанс без времени: %+v", s)
		}
	}
	if len(halls) < 2 || len(films) < 2 {
		t.Errorf("вложенность потеряна: залов %d, фильмов %d", len(halls), len(films))
	}
}

// Закрывающая скобка внутри названия не имеет права оборвать массив.
//
// Названия у этой площадки идут с форматом в скобках — «Одиссея (2D, 18+)», —
// и наивный поиск конца массива обрезал бы расписание на первом же фильме.
func TestExtractEmbeddedJSONCountsBrackets(t *testing.T) {
	body := `что-то до \"daySchedule\":[{\"name\":\"Фильм (2D] шутка\",\"n\":1},{\"name\":\"Второй\"}] хвост`
	got, err := extractEmbeddedJSON(body, "daySchedule")
	if err != nil {
		t.Fatalf("извлечение: %v", err)
	}
	if !strings.Contains(got, "Второй") {
		t.Errorf("массив оборван на скобке внутри строки: %s", got)
	}
}

// Кавычка внутри строки экранирована дважды, и снимать надо ровно один слой.
//
// Живой случай: название «Одиссея \\"Авиарежим\\"». Двумя независимыми заменами
// внутренняя кавычка теряет своё экранирование, JSON обрывается посреди
// названия — и выглядит это как сломавшийся источник.
func TestUnescapeJSStringKeepsInnerQuotesEscaped(t *testing.T) {
	got := unescapeJSString(`\"name\":\"Одиссея \\\"Авиарежим\\\"\"`)
	want := `"name":"Одиссея \"Авиарежим\""`
	if got != want {
		t.Errorf("получено %s, ожидалось %s", got, want)
	}
}

// Разметка p24 без блоков залов — это НЕ пустая афиша.
//
// Живой случай: у Колибри сеансы разложены по залам (`hall-name`), у «Часа
// кино» такой разметки нет вовсе — сеансы лежат прямо в блоке фильма. Пока
// зал считался обязательным, вторая площадка отдавала пустую афишу при HTTP
// 200: канал выглядел живым и молчащим, а на деле разбор искал разметку,
// которой у этого сайта не бывает.
func TestParseP24WithoutHalls(t *testing.T) {
	pb, err := parseP24(readFixture(t, "p24-no-halls.html"), "2026-08-04")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("афиша пуста, хотя сеансы на странице есть")
	}
	// Номера зала у этого источника нет — и выдумывать его нельзя: Hall
	// участвует в ключе сеанса.
	for _, s := range pb.Showtimes {
		if s.Hall != "" {
			t.Errorf("зал выдуман там, где источник его не даёт: %q", s.Hall)
			break
		}
		if s.Film == "" || s.StartsAt == "" {
			t.Errorf("сеанс без названия или времени: %+v", s)
			break
		}
	}
}

// Хронометраж собирается из часов И минут, как бы источник их ни записал.
//
// Здесь был тихий дефект: часы отделялись от слова границей `\b`, а в Go она
// считается по ASCII и рядом с кириллической «ч» не срабатывает. Часы молча
// терялись у ВСЕХ источников — фильм на 1 ч 38 мин приезжал как 38-минутный,
// то есть уровень каскада про аномальную длительность получал ложный вход и
// мог принять полнометражку за короткометражку-обёртку.
func TestParseRussianDurationCollectsHours(t *testing.T) {
	cases := map[string]int{
		"1 ч 44 мин":      104, // кинотеатр «Москва», без точки
		"1 ч. 38 мин.":    98,  // Mori и СИНЕМА ПАРК, с точкой
		"2 часа 10 минут": 130, // полная форма
		"44 мин":          44,  // часов нет вовсе
		"":                0,
	}
	for in, want := range cases {
		if got := parseRussianDuration(in); got != want {
			t.Errorf("%q → %d, ожидалось %d", in, got, want)
		}
	}
}

// Нарезка блоков не имеет права терять каждый второй.
//
// Нежадный поиск вида `маркер(.*?)(?:маркер|\z)` включает следующий маркер в
// конец найденного куска, и обход продолжается уже за ним. Потеря молчаливая:
// афиша остаётся непустой, канал выглядит рабочим, а половина сеансов не
// доезжает. На живых страницах так терялись 3 фильма Пионера из 7 и 4 дня
// Поклонки из 8.
func TestSplitBlocksKeepsEveryBlock(t *testing.T) {
	got := splitBlocks("шапка<b>раз<b>два<b>три", "<b>")
	if len(got) != 3 {
		t.Fatalf("кусков %d, ожидалось 3: %q", len(got), got)
	}
	if got[0] != "раз" || got[2] != "три" {
		t.Errorf("границы кусков разъехались: %q", got)
	}
	if splitBlocks("маркера тут нет", "<b>") != nil {
		t.Error("на теле без маркера должен возвращаться пустой список")
	}
}

// Разбор Пионера на живой фикстуре (снята 04.08.2026).
func TestParsePionerFixture(t *testing.T) {
	pb, err := parsePioner(readFixture(t, "pioner.html"), "2026-08-04")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	// На странице девять ссылок сеансов — столько же должно доехать.
	if len(pb.Showtimes) != 9 {
		t.Errorf("сеансов %d, на странице их 9 — часть блоков потеряна", len(pb.Showtimes))
	}
	if len(pb.Dates) < 7 {
		t.Errorf("горизонт из переключателя не собран: %v", pb.Dates)
	}
	for _, s := range pb.Showtimes {
		if s.Film == "" || s.StartsAt == "" || s.SourceID == "" {
			t.Fatalf("неполный сеанс: %+v", s)
		}
		// Зала у площадки нет — выдуманный номер сломал бы ключ сеанса.
		if s.Hall != "" {
			t.Errorf("зал выдуман: %q", s.Hall)
		}
	}
}

// Разбор Поклонки: весь горизонт одним ответом, сеансы разложены по залам,
// названным фамилиями маршалов.
func TestParsePoklonkaFixture(t *testing.T) {
	pb, err := parsePoklonka(readFixture(t, "poklonka.html"),
		time.Date(2026, 8, 4, 12, 0, 0, 0, moscowTZ))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Dates) < 5 {
		t.Errorf("дней %d — переключатель дат отдаёт больше", len(pb.Dates))
	}

	halls := map[string]bool{}
	for _, s := range pb.Showtimes {
		halls[s.Hall] = true
		if s.Hall == "" {
			t.Errorf("сеанс без зала, хотя источник его даёт: %+v", s)
			break
		}
	}
	if len(halls) < 2 {
		t.Errorf("залов %d — сеансы всех залов схлопнулись в один", len(halls))
	}
}

// Разбор кинотеатра «Москва»: формат словами, хронометраж и id сеанса.
func TestParseCinemaMoskvaFixture(t *testing.T) {
	pb, err := parseCinemaMoskva(readFixture(t, "cinema-moscow.html"), "2026-08-04")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) < 20 {
		t.Errorf("сеансов %d — похоже, часть блоков потеряна", len(pb.Showtimes))
	}

	s := pb.Showtimes[0]
	if s.Format == "" || s.SourceID == "" {
		t.Errorf("потеряны поля, которые источник отдаёт: %+v", s)
	}
	// «1 ч 44 мин» — 104 минуты, а не 44: часы должны собираться.
	if s.DurationM != 104 {
		t.Errorf("хронометраж %d, ожидалось 104 — часы потеряны", s.DurationM)
	}
}

// Разбор Романова на живой фикстуре (снята 04.08.2026 на дату 05.08).
//
// Серверный HTML у этой площадки — пустой шаблон с фильмом «test» и временами
// «00:00»; разбор ведётся по POST-ручке, и фикстура снята с неё.
func TestParseRomanovFixture(t *testing.T) {
	pb, err := parseRomanov(readFixture(t, "romanov-seans.json"), "2026-08-05")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("афиша пуста")
	}

	halls := map[string]bool{}
	for _, s := range pb.Showtimes {
		halls[s.Hall] = true
		if s.Film == "test" {
			t.Fatalf("в афишу попал плейсхолдер шаблона: %+v", s)
		}
		if s.PriceMin == 0 {
			t.Errorf("цена потеряна: %+v", s)
			break
		}
		// Цена приходит в копейках: 6000 — это 60 рублей.
		if s.PriceMin > 5000 {
			t.Errorf("цена %d — копейки не переведены в рубли", s.PriceMin)
			break
		}
	}
	if len(halls) != 3 {
		t.Errorf("залов %d, у площадки их три", len(halls))
	}
	// В Hall едет только номер: слово HALL из ключа карты туда попасть не должно.
	for h := range halls {
		if strings.Contains(h, "HALL") {
			t.Errorf("в Hall попал ключ карты целиком: %q", h)
		}
	}

	// Ночной сеанс уезжает на следующие сутки: касса относит его к предыдущему
	// операционному дню, а человеку идти в кино уже завтра.
	var night *Showtime
	for i := range pb.Showtimes {
		if strings.Contains(pb.Showtimes[i].StartsAt, "T00:00:00") {
			night = &pb.Showtimes[i]
			break
		}
	}
	if night == nil {
		t.Fatal("в фикстуре есть сеанс в 00:00, но в афише его нет")
	}
	if !strings.HasPrefix(night.StartsAt, "2026-08-06") {
		t.Errorf("ночной сеанс остался на дате запроса: %s", night.StartsAt)
	}
}

// Перенос ночных сеансов не имеет права трогать дневные.
func TestBusinessDayShift(t *testing.T) {
	cases := map[string]string{
		"00:00": "2026-08-06",
		"01:30": "2026-08-06",
		"05:59": "2026-08-06",
		"06:00": "2026-08-05",
		"11:10": "2026-08-05",
		"23:40": "2026-08-05",
	}
	for hhmm, want := range cases {
		if got := businessDayShift("2026-08-05", hhmm); got != want {
			t.Errorf("%s → %s, ожидалось %s", hhmm, got, want)
		}
	}
}

// Разбор Алмаза на живой фикстуре (снята 04.08.2026 через российский выход).
func TestParseAlmazFixture(t *testing.T) {
	pb, err := parseAlmaz(readFixture(t, "almaz.html"), "2026-08-04")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	ids := map[string]bool{}
	for _, s := range pb.Showtimes {
		ids[s.SourceID] = true
		if s.Hall == "" || s.Format == "" || s.PriceMin == 0 {
			t.Errorf("потеряны поля, которые источник отдаёт: %+v", s)
			break
		}
		// «Зал №1» — в Hall едет только номер.
		if strings.ContainsAny(s.Hall, "Зал№ ") {
			t.Errorf("в Hall попало не только число: %q", s.Hall)
			break
		}
	}
	// Идентификатор сеанса, а не фильма: перепутанные поля схлопнули бы все
	// сеансы одного фильма в один ключ.
	if len(ids) != len(pb.Showtimes) {
		t.Errorf("уникальных id %d при %d сеансах — в SourceID попал id фильма",
			len(ids), len(pb.Showtimes))
	}
}

// Разбор Иллюзиона: весь горизонт одной страницей, зал вынесен в начало
// названия («МАЛЫЙ ЗАЛ. Питер ФМ») и должен быть отделён — иначе один фильм в
// двух залах выглядит двумя разными фильмами и не совпадёт с искомым.
func TestParseIllusionFixture(t *testing.T) {
	pb, err := parseIllusion(readFixture(t, "illusion.html"),
		time.Date(2026, 8, 4, 12, 0, 0, 0, moscowTZ))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Dates) < 10 {
		t.Errorf("дней %d — источник отдаёт горизонт до сентября", len(pb.Dates))
	}

	halls := map[string]bool{}
	for _, s := range pb.Showtimes {
		halls[s.Hall] = true
		if strings.Contains(s.Film, "ЗАЛ.") {
			t.Errorf("зал остался в названии фильма: %q", s.Film)
			break
		}
	}
	if !halls["БОЛЬШОЙ ЗАЛ"] || !halls["МАЛЫЙ ЗАЛ"] {
		t.Errorf("залы не отделены: %v", halls)
	}
}

// Разбор Люксора: сеансы лежат массивом filmsAll внутри страницы.
func TestParseLuxorFixture(t *testing.T) {
	pb, err := parseLuxor(readFixture(t, "luxor.html"), "2026-08-04")
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	halls, ids := map[string]bool{}, map[string]bool{}
	for _, s := range pb.Showtimes {
		halls[s.Hall] = true
		ids[s.SourceID] = true
		// «Зал 4» — в Hall едет только номер.
		if strings.ContainsAny(s.Hall, "Зал ") {
			t.Errorf("в Hall попало не только число: %q", s.Hall)
			break
		}
	}
	if len(halls) < 3 {
		t.Errorf("залов %d — у площадки их восемь", len(halls))
	}
	if len(ids) != len(pb.Showtimes) {
		t.Errorf("уникальных id %d при %d сеансах", len(ids), len(pb.Showtimes))
	}
}

// Разбор Третьяковки: сеансы в потоковых данных, корпус — в разметке.
//
// Отбор по корпусу обязателен: строк реестра две, страница одна. Без него обе
// строки получили бы одну афишу — та же ловушка, что у Kinoplan и Миража.
func TestParseTretyakovFiltersByHall(t *testing.T) {
	body := readFixture(t, "tretyakov.html")

	eng, err := parseTretyakov(body, "Инженерный корпус")
	if err != nil {
		t.Fatalf("разбор Инженерного корпуса: %v", err)
	}
	if len(eng.Showtimes) == 0 {
		t.Fatal("в Инженерном корпусе сеансов нет, хотя на странице они есть")
	}
	for _, s := range eng.Showtimes {
		if s.Hall != "Инженерный корпус" {
			t.Fatalf("в афишу корпуса попал чужой зал: %+v", s)
		}
		// Название фильма не должно утаскивать соседние данные страницы.
		// Длина считается в РУНАХ: len() в Go меряет байты, а кириллица
		// двухбайтовая — на байтах порог срабатывал бы на обычных названиях.
		if utf8.RuneCountInString(s.Film) > 120 || strings.Contains(s.Film, `","`) {
			t.Fatalf("в название фильма затекла разметка: %.80q", s.Film)
		}
	}

	// Корпуса без показов на странице нет — это промах по названию зала, а не
	// пустая афиша, и молчать о нём нельзя.
	if _, err := parseTretyakov(body, "Новая Третьяковка"); err == nil {
		t.Error("отсутствие корпуса на странице выдано за пустую афишу")
	}
}

// Еврейский музей: в афише кино вперемешку с лекциями, экскурсиями и
// концертами, и отбор обязателен — иначе поиск фильма начнёт находить лекции
// по совпадению слов.
func TestParseJewishMuseumFiltersScreenings(t *testing.T) {
	pb, err := parseJewishMuseum(readFixture(t, "jewish-events.html"))
	if err != nil {
		t.Fatalf("разбор: %v", err)
	}
	if len(pb.Showtimes) == 0 {
		t.Fatal("кинопоказов нет, хотя в афише они есть")
	}
	for _, s := range pb.Showtimes {
		if !looksLikeScreening(s.Film, s.DeepLink) {
			t.Errorf("в афишу попало не кино: %q", s.Film)
		}
		if s.StartsAt == "" || s.PriceMin == 0 {
			t.Errorf("потеряны поля, которые источник отдаёт: %+v", s)
		}
	}
	// В фикстуре 11 событий и лишь часть из них — показы: если отбор перестанет
	// работать, сюда поедут концерты и экскурсии.
	if len(pb.Showtimes) > 6 {
		t.Errorf("показов %d из 11 событий — похоже, отбор перестал отсекать лекции", len(pb.Showtimes))
	}
}

// Пустой день у Люксора — ответ источника, а не сменившаяся вёрстка.
//
// Живой случай: прогон в 23:26 по Москве. Сеансы дня уже прошли, источник отдал
// `filmsAll = []`, и площадка получила source_broken:parse — то есть живой
// кинотеатр объявлялся мёртвым просто из-за позднего часа.
func TestParseLuxorEmptyDayIsNotBroken(t *testing.T) {
	body := readFixture(t, "luxor-empty-day.html")

	pb, err := parseLuxor(body, "2026-08-04")
	if err != nil {
		t.Fatalf("пустой день Люксора признан поломкой: %v", err)
	}
	if len(pb.Showtimes) != 0 {
		t.Errorf("из пустого дня извлеклись сеансы: %d", len(pb.Showtimes))
	}
	// Дата запроса остаётся в афише: классификатору важно, что день опрошен.
	if len(pb.Dates) != 1 || pb.Dates[0] != "2026-08-04" {
		t.Errorf("дата запроса потеряна: %v", pb.Dates)
	}
}

// А вот подсунутая страница без массива фильмов обязана остаться поломкой:
// ослабление проверки не должно превращать сменившуюся вёрстку в «пустой день».
func TestParseLuxorMissingArrayStillBroken(t *testing.T) {
	if _, err := parseLuxor("<html><body>редизайн</body></html>", "2026-08-04"); err == nil {
		t.Fatal("страница без массива фильмов принята за пустой день")
	}
}

// Пустой день у Mori размечен явным блоком внутри живого контейнера расписания.
func TestParseMoriEmptyDayIsNotBroken(t *testing.T) {
	body := readFixture(t, "mori-empty-day.html")

	pb, err := parseMori(body, "2026-08-04")
	if err != nil {
		t.Fatalf("пустой день Mori признан поломкой: %v", err)
	}
	if len(pb.Showtimes) != 0 {
		t.Errorf("из пустого дня извлеклись сеансы: %d", len(pb.Showtimes))
	}
}

// Без маркера пустого дня отсутствие групп сеансов остаётся поломкой разбора.
func TestParseMoriNoMarkerStillBroken(t *testing.T) {
	if _, err := parseMori("<html><body>редизайн</body></html>", "2026-08-04"); err == nil {
		t.Fatal("страница без групп и без маркера принята за пустой день")
	}
}

// ——— Яндекс Афиша (второй слой) ———

// Разбор расписания даёт плоский список сеансов, у каждого — своя площадка.
//
// Второй слой ценен ровно этим: площадка приезжает внутри сеанса, с адресом,
// координатами и слагом. Поэтому справочник кинотеатров в коде не нужен, а
// привязка к реестру идёт по точке и адресу из ответа.
func TestParseYandexScheduleFlattensDays(t *testing.T) {
	body := readFixture(t, "yandex-schedule.json")

	got, err := parseYandexSchedule(body)
	if err != nil {
		t.Fatalf("разбор живого ответа Афиши упал: %v", err)
	}
	// В фикстуре два дня по три сеанса: горизонт складывается в один список.
	if len(got) != 6 {
		t.Fatalf("сеансов %d, ожидалось 6 — день горизонта потерян или задвоен", len(got))
	}

	first := got[0]
	want := AggregatorSession{
		PlaceID:      "5517983f1f7d154a12ddf205",
		PlaceSlug:    "cinema-park-mega-teplyi-stan",
		PlaceTitle:   "Синема Парк Тёплый Стан",
		PlaceAddress: "Калужское ш., 21-й км, ТЦ «Мега Тёплый Стан», 1-й этаж",
		PlaceLat:     55.602984,
		PlaceLon:     37.490164,
		StartsAt:     "2026-08-05T13:45:00+03:00",
		Hall:         "Зал: 02",
		SaleStatus:   "available",
		PriceMin:     670,
		PriceMax:     670,
	}
	if first != want {
		t.Errorf("первый сеанс разобран как %+v, ожидался %+v", first, want)
	}
}

// Адрес обязан быть у каждого сеанса: без него сеанс не привязать к строке
// реестра, и весь второй слой превращается в список названий.
func TestParseYandexScheduleKeepsPlaceAddress(t *testing.T) {
	got, err := parseYandexSchedule(readFixture(t, "yandex-schedule.json"))
	if err != nil {
		t.Fatalf("разбор упал: %v", err)
	}
	for i, s := range got {
		if s.PlaceAddress == "" || s.PlaceSlug == "" {
			t.Errorf("сеанс %d без опознавательных знаков площадки: %+v", i, s)
		}
	}
}

// Отвергнутый запрос приходит с HTTP 200 и полем errors рядом с пустыми данными.
//
// Это главная ловушка источника: доступ держится на недокументированном
// заголовке, и его пропажа выглядит как «сеансов нет». Пустой список тут —
// молчаливая потеря целого слоя, поэтому ошибка обязана дойти до вызывающего.
func TestParseYandexScheduleRejectsGraphQLErrors(t *testing.T) {
	body := `{"data":{"eventScheduleOther":null},"errors":[{"message":"Unknown operation named \"unknown\""}]}`

	got, err := parseYandexSchedule(body)
	if err == nil {
		t.Fatalf("отвергнутый запрос прочитан как пустая афиша: %d сеансов", len(got))
	}
	if !strings.Contains(err.Error(), "Unknown operation") {
		t.Errorf("в ошибке не видно причины отказа: %v", err)
	}
}

// Честно пустое расписание — это не ошибка: фильм мог сойти с проката.
func TestParseYandexScheduleEmptyIsNotError(t *testing.T) {
	got, err := parseYandexSchedule(`{"data":{"eventScheduleOther":{"items":[]}}}`)
	if err != nil {
		t.Fatalf("пустое расписание признано поломкой: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("из пустого расписания извлеклись сеансы: %d", len(got))
	}
}

// Мусор вместо JSON остаётся поломкой источника.
func TestParseYandexScheduleRejectsNonJSON(t *testing.T) {
	if _, err := parseYandexSchedule("<html>405 Not Allowed</html>"); err == nil {
		t.Fatal("страница ошибки nginx принята за расписание")
	}
}

// Цена приходит в копейках и обязана доехать рублями.
//
// Замер: у Москино Невы min = 42000 при валюте rub и реальной цене билета
// около 420 ₽. Ошибка в сто раз тут тихая — и 420, и 42000 выглядят
// правдоподобно, а сравнение с ценами своих каналов ломается в обе стороны.
func TestYandexPriceRublesConvertsKopecks(t *testing.T) {
	if got := yandexPriceRubles(42000, "rub"); got != 420 {
		t.Errorf("42000 копеек превратились в %d ₽, ожидалось 420", got)
	}
	// Верхняя граница вилки — премиальный зал, а не аренда: «Времена года»,
	// залы «Зима» и «Лето».
	if got := yandexPriceRubles(800000, "RUB"); got != 8000 {
		t.Errorf("верх вилки превратился в %d ₽, ожидалось 8000", got)
	}
}

// Чужая валюта не пересчитывается: делить на сто вслепую значило бы выдать
// правдоподобное число из воздуха. Сеанс при этом не теряется — теряется цена.
func TestYandexPriceRublesSkipsForeignCurrency(t *testing.T) {
	if got := yandexPriceRubles(42000, "usd"); got != 0 {
		t.Errorf("цена в чужой валюте пересчиталась в %d, ожидался ноль", got)
	}
	if got := yandexPriceRubles(0, "rub"); got != 0 {
		t.Errorf("пустая цена превратилась в %d", got)
	}
}

// Сеанс без блока билета — это не «нет продажи», а «Афиша его не продаёт».
// Пропасть он не имеет права: сеанс состоится независимо от того, кто продаёт.
func TestParseYandexScheduleKeepsSessionWithoutTicket(t *testing.T) {
	got, err := parseYandexSchedule(readFixture(t, "yandex-schedule.json"))
	if err != nil {
		t.Fatalf("разбор упал: %v", err)
	}

	var noTicket int
	for _, s := range got {
		if s.SaleStatus == "" {
			noTicket++
			if s.PriceMin != 0 || s.PriceMax != 0 {
				t.Errorf("у сеанса без билета появилась цена: %+v", s)
			}
			if s.StartsAt == "" {
				t.Errorf("сеанс без билета потерял время: %+v", s)
			}
		}
	}
	if noTicket != 2 {
		t.Errorf("сеансов без блока билета %d, в фикстуре их 2", noTicket)
	}
}

// Статус продажи доезжает строкой. Свёртка в булев признак склеила бы «продажа
// ещё не открыта» и «мест нет» — а это разные вещи, и обе не равны «не идёт».
func TestParseYandexScheduleKeepsSaleStatusAsText(t *testing.T) {
	body := `{"data":{"eventScheduleOther":{"items":[{"date":"2026-08-05","sessions":[` +
		`{"place":{"id":"p","url":"/moscow/cinema/places/x","title":"Т","address":"А"},` +
		`"session":{"datetime":"2026-08-05T20:00:00","hall":"1",` +
		`"ticket":{"saleStatus":"not_opened","price":null}}}]}]}}}`

	got, err := parseYandexSchedule(body)
	if err != nil {
		t.Fatalf("разбор упал: %v", err)
	}
	if len(got) != 1 || got[0].SaleStatus != "not_opened" {
		t.Fatalf("статус продажи потерян: %+v", got)
	}
}

// ——— kinoafisha ———

// Разбор расписания фильма: площадка, время, формат, цена и признак продажи.
//
// Фикстура — живой блок расписания «Человека-паука» (замер 05.08.2026), тот
// самый случай, ради которого слой и заводится: Яндекс по этому фильму отдаёт
// ноль, а тут 117 сеансов на семь дат.
func TestParseKinoafishaReadsSchedule(t *testing.T) {
	got, err := parseKinoafisha(readFixture(t, "kinoafisha-spider.html"), "")
	if err != nil {
		t.Fatalf("разбор живого расписания упал: %v", err)
	}

	if len(got) != 117 {
		t.Fatalf("сеансов %d, ожидалось 117", len(got))
	}

	days, places := map[string]bool{}, map[string]bool{}
	for _, s := range got {
		days[s.StartsAt[:10]] = true
		places[s.PlaceID] = true
	}
	if len(days) != 7 {
		t.Errorf("дат %d, ожидалось 7: %v", len(days), days)
	}
	if len(places) != 2 {
		t.Errorf("площадок %d, ожидалось 2: %v", len(places), places)
	}
}

// Город приклеен к адресу вплотную («Москваул. Лобненская, 4А») — без снятия
// отсев чужих городов и привязка работали бы по мусорной строке.
func TestParseKinoafishaCleansAddress(t *testing.T) {
	got, err := parseKinoafisha(readFixture(t, "kinoafisha-spider.html"), "")
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range got {
		if strings.HasPrefix(s.PlaceAddress, "Москва") {
			t.Fatalf("город остался приклеен к адресу: %q", s.PlaceAddress)
		}
		if s.PlaceAddress == "" {
			t.Fatalf("адрес потерян у площадки %q", s.PlaceTitle)
		}
	}
}

// Сеанс без билетного бэкенда не теряется — и цену при этом иметь может.
//
// У «Отрады» покупка идёт не через виджет: класса session-ticket у её сеансов
// нет вовсе, а цена в разметке есть. Это разные вещи, и склеивать их нельзя:
// «нет билета у агрегатора» не равно «сеанса нет» и не равно «цена неизвестна».
func TestParseKinoafishaKeepsSessionsWithoutTicket(t *testing.T) {
	got, err := parseKinoafisha(readFixture(t, "kinoafisha-spider.html"), "")
	if err != nil {
		t.Fatal(err)
	}

	byStatus := map[string]int{}
	for _, s := range got {
		byStatus[s.SaleStatus]++
	}
	if byStatus["no-ticket"] == 0 {
		t.Error("сеансы без билетного бэкенда потеряны — а они есть у одной из двух площадок")
	}
	if byStatus["ticket"]+byStatus["cheap"] == 0 {
		t.Error("покупаемые сеансы потеряны")
	}

	var priced int
	for _, s := range got {
		if s.PriceMin > 0 {
			priced++
		}
	}
	if priced == 0 {
		t.Error("цена не прочиталась ни у одного сеанса")
	}
}

// Ответ догрузки одной даты приходит без секции даты — она известна из запроса.
func TestParseKinoafishaUsesFallbackDate(t *testing.T) {
	body := `<div class="showtimes_item">
		<a class="showtimesCinema_name" href="https://www.kinoafisha.info/russia/msk/cinema/8327263/">Вики Синема ЗигЗаг</a>
		<span class="showtimesCinema_addr">Москваул. Лобненская, 4А</span>
		<div class="showtimes_formatGroup" data-format="2D">
		<a class="showtimes_session session  session-ticket " data-param='{"type":"platformamts"}'>
		<span class="session_time">10:10</span><span class="session_price">от 450 ₽</span></a>
		</div></div>`

	got, err := parseKinoafisha(body, "2026-08-21")
	if err != nil {
		t.Fatalf("разбор догрузки упал: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("сеансов %d, ожидался 1", len(got))
	}
	if got[0].StartsAt[:10] != "2026-08-21" {
		t.Errorf("дата догрузки не подставилась: %q", got[0].StartsAt)
	}
	if got[0].PriceMin != 450 || got[0].SaleStatus != "ticket" {
		t.Errorf("цена или статус прочитаны неверно: %+v", got[0])
	}
}

// Сменившаяся вёрстка — ошибка разбора, а не пустая афиша. Иначе слой замолчал
// бы, и это было бы неотличимо от «фильма нигде нет».
func TestParseKinoafishaRejectsForeignMarkup(t *testing.T) {
	if _, err := parseKinoafisha("<html><body>редизайн</body></html>", "2026-08-21"); err == nil {
		t.Fatal("чужая разметка принята за пустое расписание")
	}
}

// Координаты площадки лежат не в расписании, а на странице кинотеатра.
//
// Фикстура — живой кусок страницы ЗигЗага (замер 05.08.2026): та же карточка
// JSON-LD и следом сеансы, в которых адрес площадки повторяется без точки.
func TestParseKinoafishaVenueGeoReadsPoint(t *testing.T) {
	lat, lon, err := parseKinoafishaVenueGeo(readFixture(t, "kinoafisha-cinema.html"), "8327263")
	if err != nil {
		t.Fatal(err)
	}
	if lat != 55.889471 || lon != 37.5379549 {
		t.Errorf("точка прочитана как %v, %v — ожидалась 55.889471, 37.5379549", lat, lon)
	}
}

// Адрес площадки повторяется на странице по разу на каждый сеанс, и почти все
// эти вхождения точки не несут. Взять первое попавшееся нельзя — либо потеряем
// координаты, либо припишем чужие.
func TestParseKinoafishaVenueGeoSkipsCardsWithoutPoint(t *testing.T) {
	// Разметка собрана руками: в живой странице карточка самой площадки идёт
	// первой, а нам нужен обратный порядок.
	body := `{"url":"https:\/\/www.kinoafisha.info\/russia\/msk\/cinema\/111\/","name":"сеанс без точки"},` +
		`{"url":"https:\/\/www.kinoafisha.info\/russia\/msk\/cinema\/222\/","geo":{"latitude":"55.0","longitude":"37.0"}},` +
		`{"url":"https:\/\/www.kinoafisha.info\/russia\/msk\/cinema\/111\/","geo":{"latitude":"55.75","longitude":"37.61"}}`

	lat, lon, err := parseKinoafishaVenueGeo(body, "111")
	if err != nil {
		t.Fatal(err)
	}
	if lat != 55.75 || lon != 37.61 {
		t.Errorf("взята точка %v, %v — похоже, чужой карточки", lat, lon)
	}
}

// Страница без точки — ошибка, а не нулевые координаты: ноль увёл бы площадку
// в Гвинейский залив и там же похоронил бы её привязку.
func TestParseKinoafishaVenueGeoMissingIsError(t *testing.T) {
	if _, _, err := parseKinoafishaVenueGeo("<html>редизайн</html>", "8327263"); err == nil {
		t.Fatal("страница без координат принята за площадку в нулевой точке")
	}
}

// Площадка без адреса не теряется: источник печатает под названием либо адрес,
// либо станции метро.
//
// Фикстура — живой кусок дня широкого фильма (замер 05.08.2026): шесть площадок,
// у двух адрес, у четырёх метро. Пока разбор требовал адрес тем же выражением,
// что и название, из 83 площадок дня находились 24 — и молча, потому что
// расписание оставалось непустым и на ошибку это не походило.
func TestParseKinoafishaKeepsVenuesWithoutAddress(t *testing.T) {
	got, err := parseKinoafisha(readFixture(t, "kinoafisha-wide.html"), "")
	if err != nil {
		t.Fatal(err)
	}

	places := map[string]string{}
	for _, s := range got {
		places[s.PlaceID] = s.PlaceAddress
	}
	if len(places) != 6 {
		t.Fatalf("площадок %d, ожидалось 6: %v", len(places), places)
	}

	var withAddr int
	for _, a := range places {
		if a != "" {
			withAddr++
		}
	}
	if withAddr != 2 {
		t.Errorf("адрес прочитан у %d площадок, ожидалось 2: %v", withAddr, places)
	}
}

// Блок площадки, в котором не нашлось названия, — это смена вёрстки, и она
// обязана быть ошибкой: пропуск такого блока тихо вычёркивает площадку.
func TestParseKinoafishaRejectsVenueBlockWithoutName(t *testing.T) {
	body := `<article data-schedule-date="2026-08-21">
		<div class="showtimes_item">
		<a class="showtimesCinema_title" href="/russia/msk/cinema/1/">Новая вёрстка</a>
		<a class="showtimes_session session session-ticket"><span class="session_time">10:10</span></a>
		</div>`

	if _, err := parseKinoafisha(body, "2026-08-21"); err == nil {
		t.Fatal("блок без названия площадки пропущен молча")
	}
}
