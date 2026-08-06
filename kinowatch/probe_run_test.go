package main

// Тесты сборки опроса: чтение реестра, судьба неопрошенных площадок и запрет
// выводить «фильма нет» по неполному горизонту.

import (
	"os"
	"strings"
	"testing"
)

// «Нет» — утверждение обо всём горизонте, и по дырявому горизонту оно
// недоказуемо: фильм мог идти ровно в тот день, который канал не отдал.
func TestHorizonGapForbidsAbsent(t *testing.T) {
	got := applyHorizonGap(
		ProbeResult{Status: statusAbsent, Alive: true, Evidence: "сеансов 40, фильмов 6"},
		[]string{"2026-08-05", "2026-08-06"})

	if got.Status != statusSuspect {
		t.Errorf("статус %q, ожидался %q", got.Status, statusSuspect)
	}
	if !strings.Contains(got.Evidence, "2026-08-05") {
		t.Errorf("в обосновании не видно пропущенных дат: %q", got.Evidence)
	}
	// Источник ответил за остальные дни — живость никуда не делась.
	if !got.Alive {
		t.Error("частичный отказ горизонта отменил доказанную живость источника")
	}
	// Прежнее обоснование не выбрасывается: по нему видно, что источник дал.
	if !strings.Contains(got.Evidence, "сеансов 40") {
		t.Errorf("прежнее обоснование потеряно: %q", got.Evidence)
	}
}

// Находка положительна: её дырявый горизонт не отменяет.
func TestHorizonGapKeepsFindings(t *testing.T) {
	for _, status := range []string{statusOnSale, statusFound} {
		got := applyHorizonGap(ProbeResult{Status: status, Alive: true}, []string{"2026-08-05"})
		if got.Status != status {
			t.Errorf("находка %q понижена до %q из-за пропущенного дня", status, got.Status)
		}
	}
}

func TestHorizonGapNoopOnFullHorizon(t *testing.T) {
	got := applyHorizonGap(ProbeResult{Status: statusAbsent, Alive: true, Evidence: "e"}, nil)
	if got.Status != statusAbsent || got.Evidence != "e" {
		t.Errorf("полный горизонт изменил вердикт: %+v", got)
	}
}

// Реестр принимается и отчётом --enrich, и голым списком наблюдений.
func TestReadRegistryAcceptsBothShapes(t *testing.T) {
	report := `{"inScope":1,"observations":[{"key":"1","name":"КАРО 6 Щука","fields":{"sourcekind":"karo"}}]}`
	list := `[{"key":"1","name":"КАРО 6 Щука","fields":{"sourcekind":"karo"}}]`

	for name, in := range map[string]string{"отчёт": report, "список": list} {
		obs, err := readRegistry(strings.NewReader(in))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if len(obs) != 1 || obs[0].Fields[fSourceKind] != kindKaro {
			t.Errorf("%s: разобрано неверно: %+v", name, obs)
		}
	}
}

// Пустой вход — отказ, а не ноль площадок: молча напечатанный пустой отчёт
// читается как «фильма нигде нет».
func TestReadRegistryRejectsEmpty(t *testing.T) {
	for _, in := range []string{"", "   \n", "[]"} {
		if _, err := readRegistry(strings.NewReader(in)); err == nil {
			t.Errorf("пустой вход %q принят без ошибки", in)
		}
	}
}

// У каждой неопрошенной площадки причина словами: непокрытая площадка и
// площадка без сеансов по своей природе — разные вещи.
func TestSkipReasonAlwaysExplains(t *testing.T) {
	cases := []CinemaObservation{
		{Fields: map[string]string{fStatusClass: classCloneOf}},
		{Fields: map[string]string{fStatusClass: classNoOnlineSale}},
		{Fields: map[string]string{fStatusClass: classUncovered}},
		{Fields: map[string]string{fStatusClass: classUncovered, fLastError: "нет в справочнике собственной сети"}},
		{Fields: map[string]string{}},
	}
	for _, o := range cases {
		if r := skipReason(o); strings.TrimSpace(r) == "" {
			t.Errorf("площадка с полями %v осталась без причины пропуска", o.Fields)
		}
	}
}

// Вывод «фильма нет» не действует за краем того, что источник вообще покрыл.
//
// Живой случай: КАРО отдаёт 23 даты при запрошенных 28. Про оставшиеся пять он
// не сказал ничего, и уверенное «нет» по ним — утверждение без основания.
func TestApplySourceWindowDowngradesAbsent(t *testing.T) {
	res := applySourceWindow(
		ProbeResult{Status: statusAbsent, Alive: true, Evidence: "афиша разобрана"},
		[]string{"2026-09-01", "2026-09-02"})

	if res.Status != statusSuspect {
		t.Errorf("статус %q, ожидался %q", res.Status, statusSuspect)
	}
	if !strings.Contains(res.Evidence, "2026-09-01") {
		t.Errorf("непокрытые даты не названы: %s", res.Evidence)
	}
	// Живость доказана ответом источника и от вывода о фильме не зависит: по
	// ней считается покрытие.
	if !res.Alive {
		t.Error("живость источника снята вместе с выводом о фильме")
	}
}

// Полностью покрытое окно вывод не трогает.
func TestApplySourceWindowKeepsAbsentWhenCovered(t *testing.T) {
	res := applySourceWindow(ProbeResult{Status: statusAbsent, Alive: true}, nil)
	if res.Status != statusAbsent {
		t.Errorf("статус %q, ожидался %q", res.Status, statusAbsent)
	}
}

// Находка не понижается: она положительна и уже сделана, а неполнота окна
// ставит под сомнение только отрицание.
func TestApplySourceWindowKeepsFinding(t *testing.T) {
	res := applySourceWindow(ProbeResult{Status: statusOnSale, Alive: true}, []string{"2026-09-01"})
	if res.Status != statusOnSale {
		t.Errorf("находка понижена до %q", res.Status)
	}
}

// Окно продаж искомого фильма считается по его сеансам.
func TestFoundWindowSpansFoundShowtimes(t *testing.T) {
	from, to := foundWindow([]FoundShowtime{
		{StartsAt: "2026-08-26T22:00:00+03:00"},
		{StartsAt: "2026-08-20T10:10:00+03:00"},
		{StartsAt: "2026-08-23T15:20:00+03:00"},
	})
	if from != "2026-08-20" || to != "2026-08-26" {
		t.Errorf("окно продаж %q…%q, ожидалось 2026-08-20…2026-08-26", from, to)
	}
}

// Сводка отвечает на вопрос «где уже продают», а не «сколько сеансов всего».
func TestBuildSalesSummaryTakesEarliestDate(t *testing.T) {
	got := buildSalesSummary([]VenueProbe{
		{Key: "1", SaleFrom: "2026-08-22", Found: []FoundShowtime{{}, {}}},
		{Key: "2", SaleFrom: "2026-08-20", Found: []FoundShowtime{{}}},
		{Key: "3"}, // фильма нет — в сводку не идёт
	}, nil)

	if got.EarliestDate != "2026-08-20" {
		t.Errorf("самая ранняя дата %q, ожидалось 2026-08-20", got.EarliestDate)
	}
	if got.Venues != 2 || got.Showtimes != 3 {
		t.Errorf("площадок %d, сеансов %d — ожидалось 2 и 3", got.Venues, got.Showtimes)
	}
}

// Фильм не найден нигде — сводка пустая, а не с нулевой датой.
func TestBuildSalesSummaryEmpty(t *testing.T) {
	got := buildSalesSummary([]VenueProbe{{Key: "1"}}, nil)
	if got.EarliestDate != "" || got.Venues != 0 {
		t.Errorf("пустая сводка выглядит как %+v", got)
	}
}

// Записи из кода достраивают реестр, поданный прогону на вход.
//
// Живой случай, стоивший трёх неверных объяснений подряд: строка ЗигЗага (7458)
// в отчёте прогона стояла как «канала нет: uncovered», хотя канал в коде есть и
// живьём отдаёт 70 сеансов. Применение записей жило только в --enrich, а прогон
// брал реестр со stdin как есть.
func TestApplyStandaloneRecordsFillsChannel(t *testing.T) {
	obs := []CinemaObservation{
		{Key: "7458", Name: "ЗигЗаг", Fields: map[string]string{fStatusClass: classUncovered}},
	}

	got := applyStandaloneRecords(obs)

	if got.Channels == 0 {
		t.Fatalf("ни одна запись канала не применилась: %+v", got)
	}
	if obs[0].Fields[fSourceKind] != kindKinoplan {
		t.Errorf("канал строки = %q, ожидался %q", obs[0].Fields[fSourceKind], kindKinoplan)
	}
	// Класс «непокрыта» снимается: строка перестала быть непокрытой ровно
	// потому, что канал у неё теперь есть.
	if obs[0].Fields[fStatusClass] == classUncovered {
		t.Error("строка осталась непокрытой при проставленном канале")
	}
}

// Строку-клон применение каналов пропускает молча — она не считается ни
// применённой, ни сиротой. Иначе один физический сеанс писался бы дважды.
func TestApplyStandaloneRecordsSkipsClone(t *testing.T) {
	obs := []CinemaObservation{
		{Key: "7458", Name: "ЗигЗаг", Fields: map[string]string{fStatusClass: classCloneOf}},
	}

	got := applyStandaloneRecords(obs)

	if obs[0].Fields[fSourceKind] != "" {
		t.Errorf("клон получил канал: %q", obs[0].Fields[fSourceKind])
	}
	// Сироты тут неизбежны — в реестре одна строка, а записей десятки. Важно
	// другое: САМ клон сиротой не считается, он именно пропущен.
	for _, o := range got.Orphans {
		if strings.Contains(o, "7458") {
			t.Errorf("пропущенный клон записан в сироты: %s", o)
		}
	}
}

// Запись без строки реестра обязана быть видна: это либо опечатка в
// идентификаторе, либо площадка, выпавшая из листинга ЕАИС.
func TestApplyStandaloneRecordsReportsOrphans(t *testing.T) {
	got := applyStandaloneRecords([]CinemaObservation{
		{Key: "нет-такой-строки", Name: "Пустышка", Fields: map[string]string{}},
	})

	if len(got.Orphans) == 0 {
		t.Fatal("записи без строк реестра пропали молча")
	}
	if !strings.Contains(got.Orphans[0], "без строки реестра") {
		t.Errorf("причина непонятна: %q", got.Orphans[0])
	}
}

// Повторное применение к уже обогащённому реестру ничего не меняет — числа
// показывают работу, а не переработку.
func TestApplyStandaloneRecordsIsIdempotent(t *testing.T) {
	obs := []CinemaObservation{
		{Key: "7458", Name: "ЗигЗаг", Fields: map[string]string{fStatusClass: classUncovered}},
	}

	first := applyStandaloneRecords(obs)
	kind, params := obs[0].Fields[fSourceKind], obs[0].Fields[fSourceParams]
	second := applyStandaloneRecords(obs)

	if obs[0].Fields[fSourceKind] != kind || obs[0].Fields[fSourceParams] != params {
		t.Error("повторное применение переписало канал")
	}
	if first.Channels != second.Channels {
		t.Errorf("числа применённого разошлись: %d против %d", first.Channels, second.Channels)
	}
}

// Снимок прошлого прогона старой формы читается наравне с новым.
//
// Иначе переход на список слоёв обнулил бы всю накопленную базу сравнения:
// разбор снимка падает через fail и роняет прогон целиком.
func TestReadPreviousRunAcceptsLegacyAggregator(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/legacy.json"
	body := `{"fetchedAt":"2026-08-05T09:00:00Z","days":28,
		"film":{"title":"Майкл"},
		"aggregator":{"source":"yandex-afisha","sessions":653}}`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}

	got, err := readPreviousRun(path)
	if err != nil {
		t.Fatalf("снимок старой формы не прочитался: %v", err)
	}
	if len(got.Aggregators) != 1 {
		t.Fatalf("слой старой формы потерян: %+v", got.Aggregators)
	}
	if got.Aggregators[0].Source != "yandex-afisha" || got.Aggregators[0].Sessions != 653 {
		t.Errorf("слой прочитан неверно: %+v", got.Aggregators[0])
	}
	// Поле старой формы после переноса пустое: наружу оно не пишется.
	if got.LegacyAggregator != nil {
		t.Error("старое поле осталось заполненным и уедет в новый отчёт")
	}
}

// Снимок новой формы читается как есть.
func TestReadPreviousRunReadsLayerList(t *testing.T) {
	dir := t.TempDir()
	path := dir + "/new.json"
	body := `{"fetchedAt":"2026-08-05T09:00:00Z","days":28,"film":{"title":"Майкл"},
		"aggregators":[{"source":"yandex-afisha"},{"source":"kinoafisha"}]}`
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}

	got, err := readPreviousRun(path)
	if err != nil {
		t.Fatalf("снимок новой формы не прочитался: %v", err)
	}
	if len(got.Aggregators) != 2 {
		t.Errorf("слоёв прочитано %d, ожидалось 2", len(got.Aggregators))
	}
}
