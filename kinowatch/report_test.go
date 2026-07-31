package main

// Тесты идентичности сеанса и сведения параллельных прогонов.
//
// Это самая опасная часть инструмента: ошибка здесь не роняет прогон, а тихо
// удаляет живую находку или выдаёт старый сеанс за новый. Поэтому проверяются
// не только совпадения, но и все случаи, когда схлопывать НЕЛЬЗЯ.

import (
	"testing"
)

func obs(t *testing.T, cinema string, s Showtime, source, now string) ShowtimeObservation {
	t.Helper()
	return buildShowtimeObservation(cinema, s, Match{By: matchExact, Confidence: confHigh}, source, now)
}

// Главный кейс: сеанс тот же, но продажи открылись и появилась цена. Элемент
// обязан остаться одним, с прежним FirstSeen, — иначе открытие продаж, ради
// которого инструмент и работает, выглядело бы новым сеансом.
func TestPriceChangeDoesNotCreateNewShowtime(t *testing.T) {
	before := Showtime{Film: "Одиссея", StartsAt: "2026-08-02T19:00:00+03:00", Format: "2D", OnSale: false}
	after := before
	after.OnSale = true
	after.PriceMin, after.PriceMax = 450, 600

	first := obs(t, "Мори Синема", before, "mori", "2026-08-01T10:00:00Z")
	second := obs(t, "Мори Синема", after, "mori", "2026-08-01T11:00:00Z")

	if first.SourceID != second.SourceID {
		t.Fatalf("отпечаток поехал от смены цены: %q → %q", first.SourceID, second.SourceID)
	}

	res := dedupShowtimes([]ShowtimeObservation{first, second})
	if len(res.Kept) != 1 {
		t.Fatalf("элементов %d, ожидался один", len(res.Kept))
	}
	kept := res.Kept[0].Fields
	if kept[sFirstSeen] != "2026-08-01T10:00:00Z" {
		t.Errorf("FirstSeen = %q — сеанс выглядит новинкой, хотя он вчерашний", kept[sFirstSeen])
	}
	if kept[sLastSeen] != "2026-08-01T11:00:00Z" {
		t.Errorf("LastSeen = %q, ожидалось время второго прогона", kept[sLastSeen])
	}
	if kept[sOnSale] != "true" {
		t.Error("открытие продаж не доехало до элемента — ровно то, ради чего инструмент работает")
	}
	if kept[sPriceMin] != "450" {
		t.Errorf("цена не обновилась: %q", kept[sPriceMin])
	}
}

// Два реальных сеанса одного фильма в один час в разных залах. Зала источник не
// отдаёт, но отдаёт свой id — по нему они и различаются.
func TestTwoHallLessSessionsWithOwnIDsStayTwo(t *testing.T) {
	a := Showtime{Film: "Одиссея", StartsAt: "2026-08-02T19:00:00+03:00", SourceID: "111", Format: "Стандарт"}
	b := Showtime{Film: "Одиссея", StartsAt: "2026-08-02T19:00:00+03:00", SourceID: "222", Format: "Комфорт"}

	res := dedupShowtimes([]ShowtimeObservation{
		obs(t, "СИНЕМА ПАРК Мозаика", a, "cinemapark", "2026-08-01T10:00:00Z"),
		obs(t, "СИНЕМА ПАРК Мозаика", b, "cinemapark", "2026-08-01T10:00:00Z"),
	})
	if len(res.Kept) != 2 {
		t.Fatalf("элементов %d, ожидалось 2: дедуп съел живую находку", len(res.Kept))
	}
	if res.Collapsed != 0 {
		t.Errorf("схлопнутых %d, ожидалось 0 — сеансы различимы по id", res.Collapsed)
	}
}

// А вот когда источник не дал ни зала, ни id, два таких сеанса неразличимы в
// принципе. Они схлопываются — и это попадает в счётчик, а не прячется.
func TestIndistinguishableSessionsCollapseAndAreCounted(t *testing.T) {
	s := Showtime{Film: "Одиссея", StartsAt: "2026-08-02T19:00:00+03:00", Format: "2D"}

	res := dedupShowtimes([]ShowtimeObservation{
		obs(t, "Пять звёзд", s, "5zvezd", "2026-08-01T10:00:00Z"),
		obs(t, "Пять звёзд", s, "5zvezd", "2026-08-01T10:00:00Z"),
	})
	if len(res.Kept) != 1 {
		t.Fatalf("элементов %d, ожидался один", len(res.Kept))
	}
	if res.Collapsed != 1 {
		t.Errorf("схлопнутых %d, ожидалась 1: потеря обязана быть видимой в runs", res.Collapsed)
	}
}

// Беззальный сеанс и сеанс с залом — разные ключи, дублем не являются.
func TestHallMakesDistinctKeys(t *testing.T) {
	withHall := Showtime{Film: "Одиссея", StartsAt: "2026-08-02T19:00:00+03:00", Hall: "3"}
	without := Showtime{Film: "Одиссея", StartsAt: "2026-08-02T19:00:00+03:00"}

	res := dedupShowtimes([]ShowtimeObservation{
		obs(t, "Каро 7 Атриум", withHall, "karo", "2026-08-01T10:00:00Z"),
		obs(t, "Каро 7 Атриум", without, "karo", "2026-08-01T10:00:00Z"),
	})
	if len(res.Kept) != 2 {
		t.Fatalf("элементов %d, ожидалось 2", len(res.Kept))
	}
}

// «Одиссея» и «Odyssey» — одно кино в двух написаниях (замерено живьём: одни и
// те же 8 сеансов с теми же ценами). Ключ обязан их свести.
func TestKeyNormalizesFilmTitle(t *testing.T) {
	ru := showtimeKey("Балтика", "2026-08-02T19:00:00+03:00", "«Одиссея» 6+", "1")
	same := showtimeKey("Балтика", "2026-08-02T19:00:00+03:00", "Одиссея", "1")
	if ru != same {
		t.Errorf("написание развело один сеанс по разным ключам:\n%q\n%q", ru, same)
	}
}

// Идентификаторы разных источников не сравниваются: у каждого своя нумерация,
// и одинаковые номера там встречаются по построению.
func TestSourceIDIsScopedToItsSource(t *testing.T) {
	s := Showtime{Film: "Одиссея", StartsAt: "2026-08-02T19:00:00+03:00", SourceID: "42"}
	a := obs(t, "Площадка", s, "karo", "2026-08-01T10:00:00Z")
	b := obs(t, "Площадка", s, "kinomax", "2026-08-01T10:00:00Z")
	if a.SourceID == b.SourceID {
		t.Errorf("id разных источников совпали: %q", a.SourceID)
	}
}

// Слияние идёт по доказательной силе, а не по свежести: находка не
// перетирается пустотой, уверенность не понижается молча.
func TestMergeKeepsStrongerEvidence(t *testing.T) {
	strong := map[string]string{
		sConfidence: confHigh, sMatchedBy: matchExact, sHall: "3",
		sFirstSeen: "2026-08-01T10:00:00Z", sLastSeen: "2026-08-01T10:00:00Z",
	}
	weak := map[string]string{
		sConfidence: confLow, sMatchedBy: matchDurationAnomaly, sHall: "",
		sFirstSeen: "2026-08-01T11:00:00Z", sLastSeen: "2026-08-01T11:00:00Z",
	}

	out := mergeShowtimes(strong, weak)
	if out[sConfidence] != confHigh {
		t.Errorf("уверенность понижена до %q", out[sConfidence])
	}
	if out[sMatchedBy] != matchExact {
		t.Errorf("объяснение находки затёрто слабым: %q", out[sMatchedBy])
	}
	if out[sHall] != "3" {
		t.Errorf("зал затёрт пустотой: %q", out[sHall])
	}
}

// Обратный ход: слабое наблюдение усиливается новым сильным.
func TestMergeUpgradesConfidenceWithItsReason(t *testing.T) {
	weak := map[string]string{sConfidence: confLow, sMatchedBy: matchDurationAnomaly}
	strong := map[string]string{sConfidence: confHigh, sMatchedBy: matchExact}

	out := mergeShowtimes(weak, strong)
	if out[sConfidence] != confHigh || out[sMatchedBy] != matchExact {
		t.Errorf("уверенность и её объяснение разъехались: %+v", out)
	}
}

// Пометки накапливаются множеством: ежечасная запись не должна стирать
// вчерашнее предупреждение, а повтор не должен его задваивать.
func TestMergeAccumulatesNotes(t *testing.T) {
	a := map[string]string{sNote: noteGreyRelease}
	b := map[string]string{sNote: noteNoRuntime + "; " + noteGreyRelease}

	out := mergeShowtimes(a, b)
	if out[sNote] != noteGreyRelease+"; "+noteNoRuntime {
		t.Errorf("пометки собраны как %q", out[sNote])
	}
}

// Добор зала вторым слоем не трогает идентичность элемента. Свой канал в
// следующем часе снова отдаст сеанс без зала и вычислит прежние ключ и
// отпечаток — пересчитай мы их здесь, upsert промахивался бы каждый час и
// рождал ложную новинку.
func TestAttachHallKeepsIdentity(t *testing.T) {
	s := Showtime{Film: "Одиссея", StartsAt: "2026-08-02T19:00:00+03:00", Format: "2D"}
	o := obs(t, "Мори Синема", s, "mori", "2026-08-01T10:00:00Z")
	keyBefore, idBefore := o.Key, o.SourceID

	attachHall(o.Fields, "5")

	if o.Fields[sHall] != "5" {
		t.Error("зал не доехал до колонки")
	}
	if o.Key != keyBefore || o.SourceID != idBefore {
		t.Errorf("идентичность поехала: ключ %q→%q, id %q→%q", keyBefore, o.Key, idBefore, o.SourceID)
	}
	if o.Fields[sKey] != keyBefore {
		t.Errorf("сохранённый ключ разошёлся с идентичностью элемента: %q", o.Fields[sKey])
	}

	// И следующий прогон того же беззального сеанса обязан попасть в тот же
	// элемент, а не завести новый.
	next := obs(t, "Мори Синема", s, "mori", "2026-08-01T11:00:00Z")
	res := dedupShowtimes([]ShowtimeObservation{o, next})
	if len(res.Kept) != 1 {
		t.Fatalf("после добора зала сеанс завёлся заново: элементов %d", len(res.Kept))
	}
	if res.Kept[0].Fields[sFirstSeen] != "2026-08-01T10:00:00Z" {
		t.Errorf("FirstSeen сдвинулся на %q — ложная новинка", res.Kept[0].Fields[sFirstSeen])
	}
}

// Добор по тройке ищет только среди беззальных и только при единственном
// кандидате. Двое и больше — слияния нет: схлопнуть наугад значит потерять
// реальный сеанс.
func TestMatchByTripleRefusesAmbiguity(t *testing.T) {
	at := "2026-08-02T19:00:00+03:00"
	hallless := func(id string) ShowtimeObservation {
		return obs(t, "Мори", Showtime{Film: "Одиссея", StartsAt: at, SourceID: id}, "mori", "now")
	}
	withHall := obs(t, "Мори", Showtime{Film: "Одиссея", StartsAt: at, Hall: "1"}, "mori", "now")

	one := []ShowtimeObservation{hallless("a"), withHall}
	if got := matchByTriple(one, "Мори", at, "Одиссея"); got == nil {
		t.Error("единственный беззальный кандидат не найден")
	}

	two := []ShowtimeObservation{hallless("a"), hallless("b"), withHall}
	if got := matchByTriple(two, "Мори", at, "Одиссея"); got != nil {
		t.Error("при двух кандидатах слияния быть не должно — иначе схлопнутся два реальных сеанса")
	}
}

// Отпечаток обязан различать сеансы, различимые источником: формат и зал в
// него входят.
func TestFingerprintSeparatesFormats(t *testing.T) {
	base := Showtime{Film: "Одиссея", StartsAt: "2026-08-02T19:00:00+03:00", Format: "2D"}
	imax := base
	imax.Format = "IMAX"

	if showtimeFingerprint("Каро", base) == showtimeFingerprint("Каро", imax) {
		t.Error("сеансы в разных форматах получили один отпечаток")
	}
	if showtimeFingerprint("Каро", base) == showtimeFingerprint("Октябрь", base) {
		t.Error("сеансы на разных площадках получили один отпечаток")
	}
}
