package main

// Сравнение прогона с предыдущим: что изменилось со вчера.
//
// Зачем отдельный раздел. Прогон отвечает на вопрос «где сейчас продают», а
// поймать открытие предпродажи по такому ответу нельзя: новая площадка стоит в
// общем списке рядом со ста старыми и ничем не выделяется. Вопрос, который
// нужен, — «у кого открылось СЕГОДНЯ», и на него отвечает только разница.
//
// Дисциплина сравнения. Снимок легко соврать: если сравнить прогоны по разным
// фильмам, всё покажется открывшимся. Поэтому фильм обязан совпасть строго, а
// окна сравниваются по пересечению — требовать равенства нельзя, иначе первый
// же прогон с другим горизонтом отключил бы весь раздел.

import (
	"fmt"
	"sort"
	"strings"
	"time"
)

// RunDiff — что изменилось против прошлого прогона.
type RunDiff struct {
	// PreviousAt — когда снят снимок, с которым сравнивали. Печатается всегда:
	// разница со вчерашним снимком и с трёхдневным — разного веса, и молча
	// выдавать вторую за свежую истину нельзя. Ровно на этом я и ошибся,
	// объявив ЗигЗаг непокрытым по снимку трёхдневной давности.
	PreviousAt string `json:"previousAt,omitempty"`

	// Skipped непустая означает, что сравнения не было, и объясняет почему.
	Skipped string `json:"skipped,omitempty"`

	// OverlapFrom и OverlapTo — границы пересечения окон. Вне их сравнивать
	// нечего: там один из прогонов просто не смотрел.
	OverlapFrom string `json:"overlapFrom,omitempty"`
	OverlapTo   string `json:"overlapTo,omitempty"`

	// Opened — площадки, где продажа открылась впервые: раньше сеансов не было
	// вовсе, теперь есть. Это и есть искомый сигнал.
	Opened []VenueChange `json:"opened,omitempty"`
	// Extended — площадки, где сеансы были, а теперь их стало больше.
	Extended []VenueChange `json:"extended,omitempty"`
	// Gone — площадки, где сеансы были, а теперь их нет. Обратный сигнал:
	// снятие с проката или поломка канала.
	Gone []VenueChange `json:"gone,omitempty"`
}

// VenueChange — изменение по одной площадке.
type VenueChange struct {
	Key  string `json:"key"`
	Name string `json:"name"`
	// Was и Now — число сеансов внутри пересечения окон.
	Was int `json:"was"`
	Now int `json:"now"`
	// SaleFrom — с какой даты продают сейчас. У открывшейся площадки это и есть
	// ответ на вопрос «с какого числа».
	SaleFrom string `json:"saleFrom,omitempty"`
}

// windowOf — окно прогона по его отчёту: от даты прогона на Days дней.
func windowOf(r ProbeReport) (string, string) {
	day := strings.TrimSpace(r.FetchedAt)
	if len(day) < 10 {
		return "", ""
	}
	from := day[:10]
	days := r.Days
	if days < 1 {
		days = 1
	}
	t, err := time.Parse("2006-01-02", from)
	if err != nil {
		return "", ""
	}
	return from, t.AddDate(0, 0, days-1).Format("2006-01-02")
}

// diffRuns сравнивает текущий прогон с предыдущим.
//
// Пустой prev — законный случай (первый прогон), и это не ошибка: раздел просто
// говорит, что сравнивать было не с чем.
func diffRuns(cur, prev ProbeReport) RunDiff {
	out := RunDiff{PreviousAt: prev.FetchedAt}

	if strings.TrimSpace(prev.FetchedAt) == "" {
		out.Skipped = "снимка прошлого прогона нет — сравнивать не с чем"
		return out
	}
	// Фильм обязан совпасть строго. Снимок по другому фильму показал бы
	// «открылось у всех», и это выглядело бы как настоящая находка.
	if normalizeFilmTitle(cur.Film.Title) != normalizeFilmTitle(prev.Film.Title) {
		out.Skipped = fmt.Sprintf("снимок снят по другому фильму (%q против %q) — сравнение бессмысленно",
			prev.Film.Title, cur.Film.Title)
		return out
	}

	curFrom, curTo := windowOf(cur)
	prevFrom, prevTo := windowOf(prev)
	from, to := maxStr(curFrom, prevFrom), minStr(curTo, prevTo)
	if from == "" || to == "" || from > to {
		out.Skipped = "окна прогонов не пересекаются — сравнивать нечего"
		return out
	}
	out.OverlapFrom, out.OverlapTo = from, to

	was := showtimesInWindow(prev, from, to)
	now := showtimesInWindow(cur, from, to)
	names := venueNames(cur, prev)

	for _, key := range sortedStrings(unionKeys(was, now)) {
		w, n := len(was[key]), len(now[key])
		ch := VenueChange{Key: key, Name: names[key], Was: w, Now: n, SaleFrom: saleFromOf(cur, key)}
		switch {
		case w == 0 && n > 0:
			out.Opened = append(out.Opened, ch)
		case w > 0 && n == 0:
			out.Gone = append(out.Gone, ch)
		case n > w:
			out.Extended = append(out.Extended, ch)
		}
	}
	return out
}

// showtimesInWindow — отпечатки найденных сеансов по площадкам внутри окна.
//
// Сравнение идёт по отпечаткам, а не по числам: иначе замена одного сеанса
// другим в то же время выглядела бы как «ничего не изменилось».
func showtimesInWindow(r ProbeReport, from, to string) map[string]map[string]bool {
	out := map[string]map[string]bool{}
	for _, vp := range r.Venues {
		for _, f := range vp.Found {
			if len(f.StartsAt) < 10 {
				continue
			}
			if day := f.StartsAt[:10]; day < from || day > to {
				continue
			}
			if out[vp.Key] == nil {
				out[vp.Key] = map[string]bool{}
			}
			out[vp.Key][foundFingerprint(f)] = true
		}
	}
	return out
}

// foundFingerprint — отпечаток найденного сеанса.
//
// Формат входит в ключ намеренно: 2D и IMAX одного времени в одном зале — два
// разных сеанса, и без формата открытие второго было бы невидимым.
func foundFingerprint(f FoundShowtime) string {
	return showtimeFingerprint("", Showtime{
		Film:     f.Title,
		StartsAt: f.StartsAt,
		Hall:     f.Hall,
		Format:   f.Format,
	})
}

func venueNames(reports ...ProbeReport) map[string]string {
	out := map[string]string{}
	for _, r := range reports {
		for _, vp := range r.Venues {
			if _, ok := out[vp.Key]; !ok {
				out[vp.Key] = vp.Name
			}
		}
	}
	return out
}

func saleFromOf(r ProbeReport, key string) string {
	for _, vp := range r.Venues {
		if vp.Key == key {
			return vp.SaleFrom
		}
	}
	return ""
}

func unionKeys(a, b map[string]map[string]bool) map[string]bool {
	out := map[string]bool{}
	for k := range a {
		out[k] = true
	}
	for k := range b {
		out[k] = true
	}
	return out
}

func sortedStrings(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func maxStr(a, b string) string {
	if a == "" || b == "" {
		return ""
	}
	if a > b {
		return a
	}
	return b
}

func minStr(a, b string) string {
	if a == "" || b == "" {
		return ""
	}
	if a < b {
		return a
	}
	return b
}
