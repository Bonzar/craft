package main

// Тесты сборки опроса: чтение реестра, судьба неопрошенных площадок и запрет
// выводить «фильма нет» по неполному горизонту.

import (
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
