package main

// Тесты каскада матчинга. Сети нет: каскад — чистая функция, и именно поэтому
// он проверяется целиком, включая исходы без находки.

import (
	"strings"
	"testing"
)

// spiderman — профиль искомого фильма в том виде, в каком он живёт в Craft.
// Обёртки и вилка хронометража взяты из разведки, а не выдуманы: «Прощание» и
// «Волшебник» встречены живьём, 107 и 152 минуты — замеренные случаи.
var spiderman = FilmProfile{
	Title:          "Человек-паук: Новый день",
	Aliases:        []string{"Spider-Man: Brand New Day", "Человек паук Новый день"},
	Patterns:       []string{`человек[- ]паук`},
	Wrappers:       []string{"Волшебник", "Прощание", "Три добрых дела"},
	DurationMin:    100,
	DurationMax:    160,
	NegativeTitles: []string{"Аватар"},
	SynopsisHints:  []string{"питер паркер", "паутин"},
}

func TestMatchExactTitle(t *testing.T) {
	m := matchShowtime(Showtime{Film: "Человек-паук: Новый день", DurationM: 130}, spiderman)
	if !m.Matched || m.By != matchExact {
		t.Fatalf("точное имя не опознано: %+v", m)
	}
	if m.Confidence != confHigh {
		t.Errorf("уверенность %q, ожидалась high", m.Confidence)
	}
}

// Возрастной рейтинг и формат приклеиваются к названию у части источников и к
// сравнению отношения не имеют.
func TestMatchIgnoresAgeAndFormat(t *testing.T) {
	for _, title := range []string{
		"Человек-паук: Новый день 6+",
		"Человек-паук: Новый день (2D)",
		"ЧЕЛОВЕК-ПАУК: НОВЫЙ ДЕНЬ",
	} {
		m := matchShowtime(Showtime{Film: title, DurationM: 130}, spiderman)
		if !m.Matched {
			t.Errorf("%q не опознан: %+v", title, m)
		}
	}
}

// Ключевой кейс из задачи: позиция называется чужим именем, а выдаёт её
// хронометраж. Без этого уровня фильм не находится вовсе.
func TestMatchWrapperWithAnomalousRuntime(t *testing.T) {
	m := matchShowtime(Showtime{Film: "Волшебник 6+", DurationM: 152}, spiderman)
	if !m.Matched {
		t.Fatalf("маскированный сеанс пропущен: %+v", m)
	}
	if m.By != matchWrapperDuration {
		t.Errorf("MatchedBy = %q, ожидался %q — по коду уровня видно, чем доказана находка", m.By, matchWrapperDuration)
	}
	if m.Confidence != confMedium {
		t.Errorf("уверенность %q: находка по обёртке слабее находки по имени", m.Confidence)
	}
	if !m.GreyRelease {
		t.Error("признак серого проката не поднят")
	}
}

// Та же обёртка, но хронометраж короткометражки — это и есть настоящая
// короткометражка, а не подмена. Ловить её нельзя.
func TestKnownWrapperWithOwnRuntimeIsNotMatch(t *testing.T) {
	m := matchShowtime(Showtime{Film: "Волшебник", DurationM: 12}, spiderman)
	if m.Matched {
		t.Errorf("короткометражка принята за фильм: %+v", m)
	}
	// Пометка при этом остаётся: название известное, просто длительность своя.
	if !hasNote(m.Notes, noteWrapperKnown) {
		t.Errorf("пометка про известную обёртку потеряна: %v", m.Notes)
	}
}

// Живой пример из разведки (Алмаз Синема): настоящее название стоит первым
// куском, прикрытие приклеено следом.
func TestMatchWrapperSplit(t *testing.T) {
	title := `Человек-паук: Новый день*(предсеанс. обсл.) + м/ф "Историю не изменить"`
	m := matchShowtime(Showtime{Film: title, DurationM: 130}, spiderman)
	if !m.Matched {
		t.Fatalf("склеенная позиция не разобрана: %+v", m)
	}
	if !strings.HasPrefix(m.By, matchWrapperSplit) {
		t.Errorf("MatchedBy = %q, ожидался уровень расщепления", m.By)
	}
	if !m.GreyRelease {
		t.Error("склейка с «предсеанс. обсл.» — прямой признак серого проката")
	}
}

// Фискальное название — факт источника. Оно поднимает флаг серого проката, но
// само по себе фильм не опознаёт: одна обёртка прикрывает несколько фильмов.
func TestFiscalNameRaisesGreyFlagButDoesNotMatch(t *testing.T) {
	m := matchShowtime(Showtime{Film: "Миньоны и монстры", FilmFiscal: "Сказка на ночь", DurationM: 95}, spiderman)
	if m.Matched {
		t.Errorf("чужой серый фильм принят за искомый: %+v", m)
	}
	if !m.GreyRelease {
		t.Error("непустое фискальное название не подняло признак серого проката")
	}
	if !hasNote(m.Notes, noteGreyRelease) {
		t.Errorf("пометка про серый прокат потеряна: %v", m.Notes)
	}
}

// Главное различие всего каскада: «фильма нет» и «проверить было нечем» —
// разные исходы. У КАРО хронометража нет вовсе, и объявлять там чистоту нельзя.
func TestNoRuntimeIsUnverifiableNotAbsent(t *testing.T) {
	withRuntime := matchShowtime(Showtime{Film: "Другое кино", DurationM: 90}, spiderman)
	if withRuntime.By != matchNone || withRuntime.Unverifiable {
		t.Errorf("источник с хронометражем: ожидался чистый «не сошлось», получено %+v", withRuntime)
	}

	noRuntime := matchShowtime(Showtime{Film: "Другое кино"}, spiderman)
	if noRuntime.By != matchNoneNoRuntime {
		t.Errorf("MatchedBy = %q, ожидался %q", noRuntime.By, matchNoneNoRuntime)
	}
	if !noRuntime.Unverifiable {
		t.Error("исход без хронометража не помечен непроверяемым — непокрытость КАРО выглядела бы отсутствием фильма")
	}
}

// Находка по одному имени на источнике без хронометража проверена слабее такой
// же находки у Киномакса, и это обязано быть видно в уверенности.
func TestTitleMatchWithoutRuntimeLowersConfidence(t *testing.T) {
	m := matchShowtime(Showtime{Film: "Человек-паук: Новый день"}, spiderman)
	if !m.Matched {
		t.Fatalf("имя совпало, но находки нет: %+v", m)
	}
	if m.Confidence != confMedium {
		t.Errorf("уверенность %q, ожидалась medium: длительностью подтвердить было нечем", m.Confidence)
	}
	if !m.Unverifiable {
		t.Error("флаг непроверяемости не выставлен")
	}
}

// Негативный список защищает уровень чистой аномалии: длинное кино само по
// себе подменой не является.
func TestNegativeTitleBlocksDurationAnomaly(t *testing.T) {
	m := matchShowtime(Showtime{Film: "Аватар", DurationM: 150}, spiderman)
	if m.Matched {
		t.Errorf("законно длинный фильм принят за подмену: %+v", m)
	}
}

func TestDurationAnomalyIsLowConfidence(t *testing.T) {
	m := matchShowtime(Showtime{Film: "Неизвестное кино", DurationM: 150}, spiderman)
	if !m.Matched || m.By != matchDurationAnomaly {
		t.Fatalf("аномалия хронометража не поймана: %+v", m)
	}
	if m.Confidence != confLow {
		t.Errorf("уверенность %q: догадка по одной длительности не может быть высокой", m.Confidence)
	}
}

// Синопсис только двигает уверенность вверх и никогда не создаёт находку сам.
func TestSynopsisBoostsButNeverMatches(t *testing.T) {
	alone := matchShowtime(Showtime{Film: "Чужое кино", DurationM: 90, Synopsis: "Питер Паркер снова в деле"}, spiderman)
	if alone.Matched {
		t.Errorf("синопсис создал находку в одиночку: %+v", alone)
	}

	boosted := matchShowtime(Showtime{Film: "Неизвестное кино", DurationM: 150, Synopsis: "Питер Паркер снова в деле"}, spiderman)
	if boosted.Confidence != confMedium {
		t.Errorf("уверенность %q, ожидалась medium: синопсис поднимает находку по длительности", boosted.Confidence)
	}
	if !hasNote(boosted.Notes, noteSynopsisHit) {
		t.Errorf("пометка про совпадение синопсиса потеряна: %v", boosted.Notes)
	}
}

// Профиль без вилки хронометража выключает уровни, на неё опирающиеся, а не
// делает их всеядными.
func TestProfileWithoutRuntimeRangeDoesNotGuess(t *testing.T) {
	p := FilmProfile{Title: "Человек-паук: Новый день", Wrappers: []string{"Волшебник"}}
	if m := matchShowtime(Showtime{Film: "Волшебник", DurationM: 152}, p); m.Matched {
		t.Errorf("без вилки хронометража каскад начал гадать: %+v", m)
	}
	if m := matchShowtime(Showtime{Film: "Человек-паук: Новый день"}, p); !m.Matched {
		t.Error("имя обязано совпадать и без вилки хронометража")
	}
}

// Битая регулярка в профиле — дефект данных, а не сеанса: уровень молча
// пропускается, остальные продолжают работать.
func TestBrokenPatternDoesNotBreakCascade(t *testing.T) {
	p := spiderman
	p.Patterns = []string{`([`}
	m := matchShowtime(Showtime{Film: "Человек-паук: Новый день", DurationM: 130}, p)
	if !m.Matched {
		t.Errorf("битая регулярка уронила весь каскад: %+v", m)
	}
}

func TestNormalizeFilmTitle(t *testing.T) {
	cases := []struct{ in, want string }{
		{"«Одиссея»", "одиссея"},
		{"Одиссея 6+", "одиссея"},
		{"Одиссея (2D)", "одиссея"},
		{"  ЧЕЛОВЕК-ПАУК:  НОВЫЙ ДЕНЬ ", "человек паук новый день"},
		{"Ёлки", "елки"},
		{"", ""},
	}
	for _, c := range cases {
		if got := normalizeFilmTitle(c.in); got != c.want {
			t.Errorf("normalizeFilmTitle(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestSplitWrapper(t *testing.T) {
	parts := splitWrapper(`Одиссея*(предсеанс. обсл.) + м/ф "Историю не изменить"`)
	if len(parts) != 2 {
		t.Fatalf("кусков %d, ожидалось 2: %v", len(parts), parts)
	}
	if !strings.Contains(parts[0], "Одиссея") {
		t.Errorf("настоящее название потеряно: %q", parts[0])
	}

	// Позиция без маркеров не режется — иначе обычные названия распадались бы.
	if one := splitWrapper("Человек-паук: Новый день"); len(one) != 1 {
		t.Errorf("название без маркеров разрезано: %v", one)
	}
}

func hasNote(notes []string, want string) bool {
	for _, n := range notes {
		if n == want {
			return true
		}
	}
	return false
}

// Кириллица в регулярках — отдельная ловушка Go: `\w` и `\b` в RE2 считаются по
// латинице, поэтому русские названия молча не матчатся. Тест держит оба места.
func TestCyrillicFormatIsStripped(t *testing.T) {
	cases := []struct{ in, want string }{
		{"Одиссея 2Д", "одиссея"},
		{"Одиссея (2Д)", "одиссея"},
		{"Одиссея МАКС 2Д", "одиссея макс"},
		// Формат внутри слова снимать нельзя — это часть названия. Дефис при
		// этом снимается, как и везде: «Человек-паук» и «Человек паук» обязаны
		// сойтись в один ключ.
		{"Кинопробы3D-мания", "кинопробы3d мания"},
	}
	for _, c := range cases {
		if got := normalizeFilmTitle(c.in); got != c.want {
			t.Errorf("normalizeFilmTitle(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestExtractWrapper(t *testing.T) {
	cases := []struct{ in, want string }{
		{`Одиссея (предсеансовое обслуживание к/ф "Прощание")`, "Прощание"},
		{`Миньоны и монстры (предсеансовое обслуживание к/ф «Сказка на ночь»)`, "Сказка на ночь"},
		{`Одиссея (предсеансовым обслуживанием фильма "Прощание")`, "Прощание"},
		// Обёртки нет — выдумывать её нельзя: она попала бы в профиль навсегда.
		{"Старый орёл", ""},
		{"Одиссея*(предсеанс. обсл.)", ""},
	}
	for _, c := range cases {
		if got := extractWrapper(c.in); got != c.want {
			t.Errorf("extractWrapper(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// Уровень общего ПУ работает по афише целиком и не срабатывает на пустых или
// нулевых кодах — иначе все источники без удостоверений слиплись бы в одну кучу.
func TestSharedLicenceIgnoresEmptyCodes(t *testing.T) {
	pb := Playbill{Showtimes: []Showtime{
		{Film: "Фильм А"}, {Film: "Фильм Б"},
		{Film: "Фильм В", LicenceID: "0"}, {Film: "Фильм Г", LicenceID: "0"},
	}}
	if got := sharedLicenceTitles(pb); len(got) != 0 {
		t.Errorf("пустые удостоверения слиплись: %v", got)
	}

	// Один и тот же фильм в нескольких сеансах общим ПУ не считается: код
	// делится с САМИМ СОБОЙ, а не с другим названием.
	same := Playbill{Showtimes: []Showtime{
		{Film: "Одиссея", LicenceID: "111"}, {Film: "Одиссея 6+", LicenceID: "111"},
	}}
	if got := sharedLicenceTitles(same); len(got) != 0 {
		t.Errorf("сеансы одного фильма приняты за улику: %v", got)
	}
}

// matchPlaybill — единственный вход, где уровень общего ПУ вообще работает:
// улику видно только по репертуару целиком.
func TestMatchPlaybillFlagsSharedLicence(t *testing.T) {
	pb := Playbill{Showtimes: []Showtime{
		{Film: "Миньоны и монстры", LicenceID: "214004624", DurationM: 100},
		{Film: "История игрушек 5", LicenceID: "214004624", DurationM: 110},
		{Film: "Старый орёл", LicenceID: "111097626", DurationM: 102},
	}}

	ms := matchPlaybill(pb, spiderman)
	if !ms[0].GreyRelease || !ms[1].GreyRelease {
		t.Error("фильмы с общим удостоверением не помечены серыми")
	}
	if ms[2].GreyRelease {
		t.Error("фильм с собственным удостоверением помечен серым")
	}
	if !hasNote(ms[0].Notes, noteSharedLicnc) {
		t.Errorf("пометка про общее ПУ потеряна: %v", ms[0].Notes)
	}
}

// ——— Опознание фильма у Яндекс Афиши ———

// Единственное совпадение по названию берётся без вопросов.
func TestPickYandexEventTakesTheOnlyOne(t *testing.T) {
	found := []YandexEvent{{ID: "id", Title: "Майкл", Slug: "maikl-2026", Year: 2026}}

	got, err := pickYandexEvent(found, FilmProfile{Title: "Майкл"})
	if err != nil {
		t.Fatalf("единственный кандидат отвергнут: %v", err)
	}
	if got.Slug != "maikl-2026" {
		t.Errorf("выбран %q", got.Slug)
	}
}

// Несколько кандидатов без года в профиле — это вопрос к Владу, а не развилка
// для догадки: порядок выдачи Афиши нам не принадлежит и завтра будет другим.
func TestPickYandexEventRefusesToGuess(t *testing.T) {
	found := []YandexEvent{
		{ID: "a", Title: "Майкл", Slug: "maikl", Year: 2020},
		{ID: "b", Title: "Майкл", Slug: "maikl-2026", Year: 2026},
	}

	_, err := pickYandexEvent(found, FilmProfile{Title: "Майкл"})
	if err == nil {
		t.Fatal("из двух кандидатов один выбран молча")
	}
	for _, want := range []string{"maikl-2026", "maikl", "слагом"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("в ошибке нет %q: %v", want, err)
		}
	}
}

// Год в профиле развилку решает.
func TestPickYandexEventUsesProfileYear(t *testing.T) {
	found := []YandexEvent{
		{ID: "a", Title: "Майкл", Slug: "maikl", Year: 2020},
		{ID: "b", Title: "Майкл", Slug: "maikl-2026", Year: 2026},
	}

	got, err := pickYandexEvent(found, FilmProfile{Title: "Майкл", Year: 2026})
	if err != nil {
		t.Fatalf("год не помог выбрать: %v", err)
	}
	if got.Slug != "maikl-2026" {
		t.Errorf("выбран %q, ожидался maikl-2026", got.Slug)
	}
}

// Ни одного идущего фильма — это ошибка опознания, а не пустая афиша: спросить
// расписание не про что.
func TestPickYandexEventEmptyIsError(t *testing.T) {
	if _, err := pickYandexEvent(nil, FilmProfile{Title: "Ничего"}); err == nil {
		t.Fatal("пустая выдача принята за найденный фильм")
	}
}

// Год карточки против года профиля — стоп, а не тихий ноль сеансов.
func TestVerifyYandexEventCatchesWrongYear(t *testing.T) {
	ev := YandexEvent{Title: "Майкл", Slug: "maikl", Year: 2020}

	err := verifyYandexEvent(ev, FilmProfile{Title: "Майкл", Year: 2026}, 0)
	if err == nil {
		t.Fatal("фильм другого года принят за искомый")
	}
	if !strings.Contains(err.Error(), "2020") || !strings.Contains(err.Error(), "2026") {
		t.Errorf("в ошибке не видно обоих годов: %v", err)
	}
}

// Хронометраж вне вилки профиля ловится так же.
func TestVerifyYandexEventCatchesWrongRuntime(t *testing.T) {
	ev := YandexEvent{Title: "Майкл", DurationMin: 88}
	p := FilmProfile{Title: "Майкл", DurationMin: 150, DurationMax: 200}

	if err := verifyYandexEvent(ev, p, 5); err == nil {
		t.Fatal("фильм с чужим хронометражом принят за искомый")
	}
}

// Главная развилка: сверять нечем И сеансов ноль — это ловушка однофамильца, и
// молчать нельзя. Сеансы есть — идём дальше: фильм нашёлся, спорить не о чем.
func TestVerifyYandexEventStopsOnBlindEmptySchedule(t *testing.T) {
	ev := YandexEvent{Title: "Майкл", Slug: "maikl", Year: 2020}
	blind := FilmProfile{Title: "Майкл"} // ни года, ни вилки хронометража

	if err := verifyYandexEvent(ev, blind, 0); err == nil {
		t.Fatal("пустое расписание при несверенной карточке прошло молча")
	}
	if err := verifyYandexEvent(ev, blind, 653); err != nil {
		t.Errorf("непустое расписание при том же профиле остановило прогон: %v", err)
	}
}

// Совпавший год пропускает пустое расписание: фильм честно сошёл с проката.
func TestVerifyYandexEventAllowsHonestEmptySchedule(t *testing.T) {
	ev := YandexEvent{Title: "Майкл", Year: 2026}

	if err := verifyYandexEvent(ev, FilmProfile{Title: "Майкл", Year: 2026}, 0); err != nil {
		t.Errorf("сверенный фильм без сеансов объявлен ошибкой: %v", err)
	}
}
