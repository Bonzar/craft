package main

// Тесты HTTP-клиента: проверяется то, от чего зависит полнота данных, а не
// работа стандартной библиотеки.

import (
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

// Банка cookie — не удобство, а условие полноты данных: у Pushka выбор площадки
// живёт в куке, и клиент без банки молча собирал бы одну площадку из трёх.
func TestSessionClientKeepsCookies(t *testing.T) {
	c := newSessionClient(10, 0)
	if c.http.Jar == nil {
		t.Fatal("у сессионного клиента нет банки cookie")
	}

	// Общий клиент банки не несёт намеренно: обход ходит по разным площадкам
	// подряд, и общее состояние означало бы утечку куки одной в запрос другой.
	if plain := newClient(10, 0); plain.http.Jar != nil {
		t.Error("банка появилась у общего клиента — кука одной площадки уедет в запрос следующей")
	}

	// Две отдельные сессии куками не обмениваются.
	if other := newSessionClient(10, 0); other.http.Jar == c.http.Jar {
		t.Error("два сессионных клиента делят одну банку")
	}
}

// К одной кассе идёт один запрос за раз, к разным — параллельно.
//
// Без этого пул воркеров превращается в восемь одновременных запросов к одному
// серверу: у «Москино» двадцать площадок на общем хосте, у «Синема Парк»
// пятнадцать. Ускорения это не даёт, а выглядит как атака.
func TestClientOneRequestPerHostAtATime(t *testing.T) {
	var mu sync.Mutex
	var now, peak int
	handler := func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		now++
		if now > peak {
			peak = now
		}
		mu.Unlock()

		time.Sleep(20 * time.Millisecond)

		mu.Lock()
		now--
		mu.Unlock()
		w.Write([]byte("ok"))
	}
	srv := httptest.NewServer(http.HandlerFunc(handler))
	defer srv.Close()

	c := newClient(10, 0)
	var wg sync.WaitGroup
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			c.getText(srv.URL)
		}()
	}
	wg.Wait()

	if peak != 1 {
		t.Errorf("к одному хосту одновременно шло %d запросов, ожидался 1", peak)
	}
}

// Разные кассы друг друга не ждут — ради этого параллельность и заводилась.
func TestClientDifferentHostsGoInParallel(t *testing.T) {
	slow := func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(120 * time.Millisecond)
		w.Write([]byte("ok"))
	}
	a := httptest.NewServer(http.HandlerFunc(slow))
	defer a.Close()
	b := httptest.NewServer(http.HandlerFunc(slow))
	defer b.Close()

	c := newClient(10, 0)
	start := time.Now()
	var wg sync.WaitGroup
	for _, u := range []string{a.URL, b.URL} {
		wg.Add(1)
		go func(u string) {
			defer wg.Done()
			c.getText(u)
		}(u)
	}
	wg.Wait()

	if d := time.Since(start); d > 200*time.Millisecond {
		t.Errorf("два разных хоста заняли %v — похоже, шли по очереди", d)
	}
}

// 429 тормозит весь хост, а не один запрос: соседние площадки той же сети
// обязаны увидеть паузу, иначе они добьют кассу, пока мы вежливо ждём.
func TestRateLimitPausesWholeHost(t *testing.T) {
	var mu sync.Mutex
	var hits int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		hits++
		first := hits == 1
		mu.Unlock()

		if first {
			w.Header().Set("Retry-After", "1")
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		w.Write([]byte("ok"))
	}))
	defer srv.Close()

	c := newClient(10, 0)
	c.getText(srv.URL) // ловит 429 и назначает паузу хосту

	start := time.Now()
	if _, err := c.getText(srv.URL); err != nil {
		t.Fatalf("второй запрос упал: %v", err)
	}
	if d := time.Since(start); d < 500*time.Millisecond {
		t.Errorf("соседний запрос ушёл через %v — пауза хоста не сработала", d)
	}
}

// Названный кассой срок читается и в секундах, и датой; чужой мусор откатывает
// к своей лесенке — «касса не сказала» не значит «можно сразу».
func TestRetryAfterReadsBothForms(t *testing.T) {
	fallback := 7 * time.Second
	if got := retryAfter("3", fallback); got != 3*time.Second {
		t.Errorf("секунды прочитаны как %v", got)
	}
	if got := retryAfter("", fallback); got != fallback {
		t.Errorf("пустой заголовок дал %v, ожидалась своя лесенка", got)
	}
	if got := retryAfter("мусор", fallback); got != fallback {
		t.Errorf("мусор дал %v, ожидалась своя лесенка", got)
	}
	if got := retryAfter(time.Now().Add(2*time.Second).UTC().Format(http.TimeFormat), fallback); got <= 0 || got > 3*time.Second {
		t.Errorf("дата прочитана как %v", got)
	}
}
