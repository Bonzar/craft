package main

// HTTP-клиент и обход листинга ЕАИС.
//
// Политика ретраев скопирована с craft-sync осознанно: два независимых бюджета
// (сетевые/5xx и 429), детерминированный бэкофф без джиттера, 4xx не ретраится.
// Причина та же — не долбить чужой сервер и не маскировать отказ доступа
// повторами.

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"strings"
	"time"
)

// browserUA — честный десктопный User-Agent.
// Не маскировка: часть сайтов режет запросы без UA как ботов, при этом мы не
// притворяемся человеком в интерфейсе и не обходим капчу.
const browserUA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
	"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

// geoUA — User-Agent для геокодеров.
// Nominatim требует идентифицирующий агент с контактом и режет анонимные
// запросы; браузерный UA там не годится именно потому, что не идентифицирует.
const geoUA = "kinowatch/0.1 (personal cinema schedule tracker; github.com/bonzar/craft)"

// geoMinInterval — не чаще одного запроса в секунду: политика Nominatim.
// Photon свой рейт не публикует, но ходим с той же вежливой частотой.
const geoMinInterval = time.Second

// acceptJSON — Overpass отвечает 406 на браузерный Accept с text/html
// (проверено живьём). JSON-источникам нужен свой.
const acceptJSON = "application/json"

const acceptHTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

type Client struct {
	http      *http.Client
	retries   int
	userAgent string
	accept    string
	// acceptLang пустой означает «не слать заголовок вовсе».
	//
	// Это не мелочь: Photon на Accept-Language: ru-RU отдаёт названия
	// ПО-АНГЛИЙСКИ — city приходит как «Moscow» вместо «Москва» (проверено
	// живьём 31.07, тот же URL без заголовка отвечает кириллицей). Гейт по
	// городу тогда отбраковывает вообще всё, и выглядит это как «геокодер
	// ничего не находит».
	acceptLang string
	// minInterval — минимальный зазор между запросами этого клиента. Ноль
	// означает «без троттлинга»: у ЕАИС четыре страницы, тормозить нечего.
	minInterval time.Duration
	lastCall    time.Time
}

func newClient(timeoutSec, retries int) *Client {
	return &Client{
		http:       &http.Client{Timeout: time.Duration(timeoutSec) * time.Second},
		retries:    retries,
		userAgent:  browserUA,
		acceptLang: "ru-RU,ru;q=0.9,en;q=0.8",
	}
}

// newSessionClient — клиент с банкой cookie: для источников, где выбор площадки
// хранится в сессии, а не в адресе запроса.
//
// Живой случай — Pushka: `/!/ajax/schedule` отдаёт расписание той площадки, чей
// идентификатор лежит в куке `cinema_id`, и ставит эту куку страница площадки.
// Голый запрос к ajax возвращает дефолт, поэтому без банки видна была бы одна
// площадка из трёх. Путь, Referer и query-параметры на выбор не влияют —
// проверено живьём всеми тремя способами.
//
// Банка НЕ добавляется в общий клиент намеренно: обход афиш ходит по разным
// площадкам подряд, и общее состояние означало бы, что кука одной площадки
// уезжает в запрос следующей — то есть тихую подмену данных.
func newSessionClient(timeoutSec, retries int) *Client {
	c := newClient(timeoutSec, retries)
	// cookiejar.New с нулевыми опциями ошибку не возвращает никогда, но
	// игнорировать её молча всё равно нельзя: сигнатура может измениться.
	if jar, err := cookiejar.New(nil); err == nil {
		c.http.Jar = jar
	}
	return c
}

// newGeoClient — клиент для Photon и Nominatim: свой честный UA и троттлинг.
// Отдельный клиент, а не флаг на общем, потому что зазор между запросами должен
// держаться на своём счётчике: обход афиш не обязан ждать секунду.
func newGeoClient(timeoutSec, retries int) *Client {
	c := newClient(timeoutSec, retries)
	c.userAgent = geoUA
	c.accept = acceptJSON
	c.acceptLang = "" // иначе Photon отвечает по-английски, см. поле acceptLang
	c.minInterval = geoMinInterval
	return c
}

// newJSONClient — для JSON-источников вроде Overpass.
//
// Отличий от общего клиента два, и оба выяснены живым запросом: Overpass
// отвечает 406 на браузерный Accept с text/html, а с браузерным User-Agent
// ответ не приходит вовсе — тот же самый URL с честным «kinowatch/0.1»
// отдаёт 200. Маскировка под браузер тут не помогает, а мешает.
func newJSONClient(timeoutSec, retries int) *Client {
	c := newClient(timeoutSec, retries)
	c.accept = acceptJSON
	c.userAgent = geoUA
	return c
}

// throttle выдерживает зазор перед очередным запросом.
func (c *Client) throttle() {
	if c.minInterval <= 0 {
		return
	}
	if !c.lastCall.IsZero() {
		if wait := c.minInterval - time.Since(c.lastCall); wait > 0 {
			time.Sleep(wait)
		}
	}
	c.lastCall = time.Now()
}

// transientBackoff — 2, 4, 8, 16 секунд.
func transientBackoff(attempt int) time.Duration {
	return time.Duration(1<<(attempt+1)) * time.Second
}

// rateLimitBackoff — 5, 10, 20, 40, 60 (потолок).
func rateLimitBackoff(attempt int) time.Duration {
	d := time.Duration(5<<attempt) * time.Second
	if d > 60*time.Second {
		return 60 * time.Second
	}
	return d
}

// getText тянет страницу как текст. Возвращает тело и ошибку; на 4xx (кроме 429)
// не ретраит — если доступ закрыт, повтор ничего не изменит, только нагрузит.
func (c *Client) getText(url string) (string, error) {
	body, _, err := c.get(url)
	return body, err
}

// get — то же самое, но отдаёт ещё и код ответа.
//
// Код нужен опросу площадок: классификатор различает 401/403/404 (протухший
// доступ, лечится заменой токена) и остальные отказы (лечатся повтором). В
// ошибке этой разницы не видно — там строка, и разбирать её обратно значило бы
// гадать по тексту.
func (c *Client) get(url string) (string, int, error) {
	return c.getHeaders(url, nil)
}

// getHeaders — тот же запрос с дополнительными заголовками.
//
// Нужны кассам, где площадка задаётся не адресом: Kinoplan различает
// приложения по x-application-token и без x-platform отвечает 400.
func (c *Client) getHeaders(url string, extra map[string]string) (string, int, error) {
	var backoff time.Duration
	netAttempt, rlAttempt := 0, 0

	for {
		if backoff > 0 {
			time.Sleep(backoff)
			backoff = 0
		}
		c.throttle()

		ctx, cancel := context.WithTimeout(context.Background(), c.http.Timeout)
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			cancel()
			return "", 0, fmt.Errorf("построение запроса: %w", err)
		}
		ua := c.userAgent
		if ua == "" {
			ua = browserUA
		}
		accept := c.accept
		if accept == "" {
			accept = acceptHTML
		}
		req.Header.Set("User-Agent", ua)
		req.Header.Set("Accept", accept)
		if c.acceptLang != "" {
			req.Header.Set("Accept-Language", c.acceptLang)
		}
		for k, v := range extra {
			req.Header.Set(k, v)
		}

		resp, err := c.http.Do(req)
		if err != nil {
			cancel()
			if netAttempt < c.retries {
				backoff = transientBackoff(netAttempt)
				netAttempt++
				continue
			}
			return "", 0, fmt.Errorf("сеть: %w", err)
		}

		body, readErr := io.ReadAll(resp.Body)
		resp.Body.Close()
		cancel()

		switch {
		case resp.StatusCode == http.StatusTooManyRequests:
			if rlAttempt < c.retries+2 {
				backoff = rateLimitBackoff(rlAttempt)
				rlAttempt++
				continue
			}
			return "", resp.StatusCode, fmt.Errorf("HTTP 429 после ретраев")

		case resp.StatusCode >= 500:
			if netAttempt < c.retries {
				backoff = transientBackoff(netAttempt)
				netAttempt++
				continue
			}
			return "", resp.StatusCode, fmt.Errorf("HTTP %d после ретраев", resp.StatusCode)

		case resp.StatusCode >= 400:
			return "", resp.StatusCode, fmt.Errorf("HTTP %d", resp.StatusCode)
		}

		if readErr != nil {
			return "", resp.StatusCode, fmt.Errorf("чтение тела: %w", readErr)
		}
		return string(body), resp.StatusCode, nil
	}
}

// maxEaisPages — предохранитель от бесконечного обхода.
// ЕАИС на несуществующем номере страницы отдаёт не 404, а первую страницу,
// поэтому цикл обязан уметь останавливаться сам (см. ниже).
const maxEaisPages = 40

// fetchAllEaisPages обходит листинг постранично.
//
// Условия останова, оба нужны:
//   - страница не дала ни одной строки;
//   - строки повторяют уже собранные ID — значит сервер вернул первую страницу
//     вместо запрошенной, и дальше идти бессмысленно.
func fetchAllEaisPages(c *Client, base, region string) ([]EaisRow, int, []string) {
	var all []EaisRow
	seen := map[string]bool{}
	var errs []string
	pages := 0

	for page := 1; page <= maxEaisPages; page++ {
		url := eaisPageURL(base, region, page)
		body, err := c.getText(url)
		if err != nil {
			errs = append(errs, fmt.Sprintf("страница %d: %v", page, err))
			break
		}

		rows := parseEaisPage(body)
		if len(rows) == 0 {
			break
		}

		fresh := 0
		for _, r := range rows {
			if seen[r.ID] {
				continue
			}
			seen[r.ID] = true
			all = append(all, r)
			fresh++
		}

		// Страница, не давшая ни одной новой строки, — это повтор предыдущей
		// (ЕАИС так отвечает на номер за пределами листинга). Она не считается
		// пройденной, иначе счётчик страниц врёт на единицу.
		if fresh == 0 {
			break
		}
		pages = page
	}

	return all, pages, errs
}

func nowRFC3339() string {
	return time.Now().UTC().Format(time.RFC3339)
}

// trimBase нормализует базовый URL.
func trimBase(s string) string {
	return strings.TrimRight(s, "/")
}
