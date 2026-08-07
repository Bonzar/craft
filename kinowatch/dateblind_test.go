package main

// Чек сходимости дат: источник, отдающий одно и то же на разные даты, не должен
// выглядеть источником, у которого сеансы есть каждый день.
//
// Живой случай, ради которого чек заведён: Мираж на любой из 28 запросов отдавал
// сегодняшнюю страницу, а разбор приписывал её сеансы запрошенному дню — в
// отчёт ехали сеансы, которых нет. Отдельные каналы починены адресом с датой,
// но признак «даты в запросе нет» опознаётся только по коду, и следующий такой
// канал появится молча. Этот чек ловит любой из них, даже неизвестный.

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

// tlsChannel поднимает локальный сервер и возвращает клиента, которому он
// доверяет, вместе с параметрами канала, ходящего на этот адрес.
//
// Канал берётся тот, у которого домен площадки задаётся реестром: остальные
// зашиты на боевые адреса, и направить их на локальный сервер нечем. Схема
// только https — адрес канала собирается по ней.
func tlsChannel(t *testing.T, h http.HandlerFunc) (*Client, ChannelParams, func()) {
	t.Helper()
	srv := httptest.NewTLSServer(h)
	c := newClient(10, 0)
	c.http = srv.Client()
	c.http.Timeout = 10 * time.Second
	return c, ChannelParams{pHost: srv.Listener.Addr().String()}, srv.Close
}

func etobiletBody(t *testing.T) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", "primecinema-today.html"))
	if err != nil {
		t.Fatalf("фикстура не читается: %v", err)
	}
	return string(b)
}

// Источник отдаёт одно и то же тело на любую дату: обход обязан остановиться на
// втором дне, а окно площадки — сжаться до одного дня с названной причиной.
func TestDateBlindSourceStopsAfterSecondDay(t *testing.T) {
	body := etobiletBody(t)

	var mu sync.Mutex
	hits := 0
	c, params, stop := tlsChannel(t, func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		hits++
		mu.Unlock()
		w.Write([]byte(body))
	})
	defer stop()

	from := time.Date(2026, 8, 7, 0, 0, 0, 0, moscowTZ)
	probe := fetchChannel(c, kindEtobilet, params, from, 28)

	if hits != 2 {
		t.Errorf("запросов %d, ожидалось 2: после совпадения тел спрашивать источник не о чем", hits)
	}
	if probe.DateBlind == "" {
		t.Error("совпадение тел не названо — площадка выглядит как «сеансов дальше нет»")
	}
	if probe.WindowFrom != probe.WindowTo {
		t.Errorf("окно источника %s..%s — в него попал день, про который он ничего не сказал",
			probe.WindowFrom, probe.WindowTo)
	}
	if probe.WindowFrom != "2026-08-07" {
		t.Errorf("окно собрано не по первому дню: %s", probe.WindowFrom)
	}
}

// Источник, честно отвечающий за каждый день, обходится целиком: чек не должен
// резать живой горизонт.
func TestDistinctBodiesGoFullHorizon(t *testing.T) {
	body := etobiletBody(t)

	var mu sync.Mutex
	hits := 0
	c, params, stop := tlsChannel(t, func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		hits++
		n := hits
		mu.Unlock()
		// Тело своё на каждый день — как у источника, который дату различает.
		w.Write([]byte(body))
		w.Write([]byte("<!-- день " + r.URL.RawQuery + " " + string(rune('a'+n%26)) + " -->"))
	})
	defer stop()

	from := time.Date(2026, 8, 7, 0, 0, 0, 0, moscowTZ)
	probe := fetchChannel(c, kindEtobilet, params, from, 5)

	if hits != 5 {
		t.Errorf("запросов %d, ожидалось 5 — чек обрубил живой горизонт", hits)
	}
	if probe.DateBlind != "" {
		t.Errorf("живой источник помечен как неразличающий даты: %q", probe.DateBlind)
	}
}

// Совпадение тел само по себе не жалоба. Решает то, сдвинулись ли даты сеансов
// вслед за запросом: сдвинулись — датируем их мы и приписывать день нечем; нет
// — источник датирует сеансы сам, и лишними были только запросы.
func TestDateBlindReasonTellsWhoDated(t *testing.T) {
	prev := bodySeen{date: "2026-08-07", days: []string{"2026-08-07"}}

	if r := dateBlindReason(prev, "2026-08-08", []string{"2026-08-08"}); r == "" {
		t.Error("даты уехали вслед за запросом, а причина не названа")
	}
	if r := dateBlindReason(prev, "2026-08-08", []string{"2026-08-07"}); r != "" {
		t.Errorf("источник датирует сеансы сам, а мы жалуемся: %q", r)
	}
}

// Источник, назвавший свои дни, дальше спрашивается только про них — и только
// про те, что попали в запрошенное окно.
//
// Без пересечения с окном ключ --days перестал бы быть верхней границей: у ГУМа
// собственный список тянется на полтора месяца вперёд, а спрашивали его про 28
// дней.
func TestNarrowPlanKeepsHorizonAsUpperBound(t *testing.T) {
	from := time.Date(2026, 8, 7, 0, 0, 0, 0, moscowTZ)
	plan, inHorizon := horizonPlan(from, 28)
	if len(plan) != 28 {
		t.Fatalf("окно разложено в %d дней вместо 28", len(plan))
	}

	// Источник назвал пять своих дней и один за краем окна.
	src := []string{"2026-08-07", "2026-08-09", "2026-08-12", "2026-09-24"}
	got := narrowPlan(plan, 0, src, inHorizon)

	var dates []string
	for _, d := range got {
		dates = append(dates, d.Format("2006-01-02"))
	}
	want := []string{"2026-08-07", "2026-08-09", "2026-08-12"}
	if !sameStrings(dates, want) {
		t.Errorf("обход сужен до %v, ожидалось %v", dates, want)
	}
}

// Источник, своего списка не знающий, обходится как раньше — по всему окну; это
// проверяет TestDistinctBodiesGoFullHorizon выше: etobilet дней не называет и
// проходит все пять.

// День без сеансов из сравнения исключён. У многих каналов пустой день — байт в
// байт одна и та же заглушка, и без этой оговорки два таких дня подряд обрубили
// бы обход, потеряв все дальнейшие дни с сеансами.
func TestEmptyDaysDoNotStopTraversal(t *testing.T) {
	body := etobiletBody(t)

	var mu sync.Mutex
	hits := 0
	c, params, stop := tlsChannel(t, func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		hits++
		n := hits
		mu.Unlock()
		// Первые три дня пустые и побайтово одинаковые, дальше — настоящая
		// афиша. Источник с таким поведением обязан быть обойден до конца.
		if n <= 3 {
			w.Write([]byte(`<html><body><div class="sessions"></div></body></html>`))
			return
		}
		w.Write([]byte(body))
	})
	defer stop()

	from := time.Date(2026, 8, 7, 0, 0, 0, 0, moscowTZ)
	probe := fetchChannel(c, kindEtobilet, params, from, 5)

	if hits < 4 {
		t.Fatalf("запросов %d — обход оборвался на пустых днях", hits)
	}
	if len(probe.Playbill.Showtimes) == 0 {
		t.Error("сеансы дней после пустых потеряны")
	}
}
