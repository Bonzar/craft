package main

// Тесты ядра реестра. Сети нет ни в одном: парсер получает зафиксированные
// фикстуры (testdata/eais-page*.html — вырезанные таблицы четырёх страниц
// московского листинга ЕАИС, снимок 31.07.2026).
//
// Почему фикстуры на диске, хотя craft-sync обходится литералами: здесь
// проверяются числа целиком (176 строк, 55 одиночек), а они выводятся только из
// полного листинга. Вырезана именно таблица, вёрстка страницы не хранится.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func loadFixtures(t *testing.T) []EaisRow {
	t.Helper()
	var all []EaisRow
	for _, name := range []string{"eais-page1.html", "eais-page2.html", "eais-page3.html", "eais-page4.html"} {
		data, err := os.ReadFile(filepath.Join("testdata", name))
		if err != nil {
			t.Fatalf("чтение фикстуры %s: %v", name, err)
		}
		all = append(all, parseEaisPage(string(data))...)
	}
	return all
}

func TestParseEaisFixtureTotals(t *testing.T) {
	rows := loadFixtures(t)

	if got, want := len(rows), 176; got != want {
		t.Fatalf("строк московского листинга: got %d, want %d", got, want)
	}

	standalone := 0
	for _, r := range rows {
		if strings.TrimSpace(r.Network) == "" {
			standalone++
		}
	}
	if got, want := standalone, 55; got != want {
		t.Errorf("строк без сети: got %d, want %d", got, want)
	}

	networks := map[string]bool{}
	for _, r := range rows {
		if n := strings.TrimSpace(r.Network); n != "" {
			networks[n] = true
		}
	}
	if got, want := len(networks), 24; got != want {
		t.Errorf("различных сетей: got %d, want %d", got, want)
	}
}

// Шапка таблицы размечена теми же div.d-tbl-item, что и данные. Наивная
// группировка по пять ячеек даёт лишнюю строку на каждой странице — 180 вместо
// 176. Этот тест держит отсечение шапки.
func TestParseEaisDropsHeaderRow(t *testing.T) {
	rows := loadFixtures(t)
	for _, r := range rows {
		if r.Region == "Регион" || r.City == "Город" || r.ID == "ID" || r.Company == "Организация" {
			t.Fatalf("шапка попала в выдачу как строка данных: %+v", r)
		}
	}
}

func TestParseEaisIDsAreUnique(t *testing.T) {
	rows := loadFixtures(t)
	seen := map[string]string{}
	for _, r := range rows {
		if prev, dup := seen[r.ID]; dup {
			t.Errorf("ID %s встречается дважды: %q и %q", r.ID, prev, r.Company)
		}
		seen[r.ID] = r.Company
	}
}

// Из региона «Москва г» вычитаются города, которые к городу в границах МКАД не
// относятся: Зеленоград (анклав далеко за кольцом), Московский и Мамыри (ТиНАО).
func TestApplyCityScopeExcludesNonMoscowCities(t *testing.T) {
	rows := loadFixtures(t)
	decisions := applyCityScope(rows)

	if len(decisions) != len(rows) {
		t.Fatalf("решений %d, строк %d — ни одна строка не должна исчезать", len(decisions), len(rows))
	}

	excluded := map[string]int{}
	for _, d := range decisions {
		if !d.InScope {
			if d.Reason == "" {
				t.Errorf("строка вне охвата без причины: %+v", d.Row)
			}
			excluded[d.Row.City]++
		}
	}

	for _, city := range []string{"Зеленоград г", "Московский г", "Мамыри д"} {
		if excluded[city] == 0 {
			t.Errorf("город %q должен быть вне охвата, но не исключён", city)
		}
	}
	if excluded["Москва г"] != 0 {
		t.Errorf("город «Москва г» исключён из охвата — этого быть не должно")
	}
}

// Ключевой инвариант: подсказка о дубле ничего не удаляет.
//
// В листинге есть шесть пар с неразличимыми названиями («PRIME CINEMA» ×2,
// «Времена года» ×2 и др.) — по названию непонятно, задвоенная это регистрация
// одной площадки или два разных зала, и решается это только адресом.
func TestDuplicateHintsDoNotRemoveRows(t *testing.T) {
	rows := loadFixtures(t)
	hints := findDuplicateHints(rows)

	if len(hints) == 0 {
		t.Fatal("в московском листинге есть повторяющиеся названия, подсказок нет")
	}

	byNorm := map[string]DuplicateHint{}
	for _, h := range hints {
		byNorm[h.Normalized] = h
	}

	for _, want := range []struct {
		norm  string
		count int
	}{
		{"prime cinema", 2},
		{"времена года", 2},
	} {
		h, ok := byNorm[want.norm]
		if !ok {
			t.Errorf("нет подсказки о дубле для %q", want.norm)
			continue
		}
		if len(h.IDs) != want.count {
			t.Errorf("подсказка %q: %d ID, ожидалось %d", want.norm, len(h.IDs), want.count)
		}
	}

	// Сами строки остались на месте — подсказка это пометка, а не удаление.
	if len(rows) != 176 {
		t.Errorf("после сбора подсказок строк %d, ожидалось 176", len(rows))
	}
}

// Обратная сторона того же инварианта: площадки одной сети, различимые по
// названию, дублями не считаются и в подсказки не попадают.
//
// Три «Pushka» (6150, 7662 Mitino, 8079 Brateevo) — это Клён, Митино и
// Братеево, три разных адреса. Схлопывание по «бренду» стёрло бы две реальные
// площадки из знаменателя покрытия.
func TestSameBrandDifferentVenuesAreNotDuplicates(t *testing.T) {
	rows := loadFixtures(t)

	byID := map[string]EaisRow{}
	for _, r := range rows {
		byID[r.ID] = r
	}
	for _, id := range []string{"6150", "7662", "8079"} {
		if _, ok := byID[id]; !ok {
			t.Fatalf("площадка %s пропала из листинга", id)
		}
	}

	for _, h := range findDuplicateHints(rows) {
		if strings.HasPrefix(h.Normalized, "pushka") && len(h.IDs) > 1 {
			t.Errorf("площадки одного бренда с разными названиями помечены дублем: %+v", h)
		}
	}
}

func TestNormalizeName(t *testing.T) {
	cases := []struct{ in, want string }{
		{"PRIME CINEMA", "prime cinema"},
		{"Времена Года", "времена года"},
		{"Времена года", "времена года"},
		{`ООО "Союз кинематографистов России"`, "союз кинематографистов россии"},
		{"ОАО \"Планетарий\"", "планетарий"},
		{"ИП Харчев М.А.", "харчев м а"},
		{"«Коперто Синема»", "коперто синема"},
		{"Ёлка", "елка"},
		{"  Лорд  ", "лорд"},
	}
	for _, c := range cases {
		if got := normalizeName(c.in); got != c.want {
			t.Errorf("normalizeName(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// Форма ?region= не работает (301 на http, потом 503) — код региона идёт путём,
// причём первая страница и последующие устроены по-разному.
func TestEaisPageURL(t *testing.T) {
	base := "https://ekinobilet.fond-kino.ru"
	cases := []struct {
		page int
		want string
	}{
		{1, base + "/demonstrators/7700000000000/"},
		{2, base + "/demonstrators/page/2/7700000000000/"},
		{4, base + "/demonstrators/page/4/7700000000000/"},
	}
	for _, c := range cases {
		if got := eaisPageURL(base+"/", moscowRegionCode, c.page); got != c.want {
			t.Errorf("eaisPageURL(page=%d) = %q, want %q", c.page, got, c.want)
		}
	}
}

func TestCleanCell(t *testing.T) {
	cases := []struct{ in, want string }{
		{"Москва г", "Москва г"},
		{"  <span>Лорд</span> ", "Лорд"},
		{"", ""},
		{"Кино&amp;Театр", "Кино&Театр"},
	}
	for _, c := range cases {
		if got := cleanCell(c.in); got != c.want {
			t.Errorf("cleanCell(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestBackoffSchedules(t *testing.T) {
	wantTransient := []time.Duration{2 * time.Second, 4 * time.Second, 8 * time.Second, 16 * time.Second}
	for i, want := range wantTransient {
		if got := transientBackoff(i); got != want {
			t.Errorf("transientBackoff(%d) = %v, want %v", i, got, want)
		}
	}

	wantRL := []time.Duration{5 * time.Second, 10 * time.Second, 20 * time.Second, 40 * time.Second, 60 * time.Second, 60 * time.Second}
	for i, want := range wantRL {
		if got := rateLimitBackoff(i); got != want {
			t.Errorf("rateLimitBackoff(%d) = %v, want %v", i, got, want)
		}
	}
}
