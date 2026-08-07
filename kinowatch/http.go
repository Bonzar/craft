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
	"net/url"
	"strconv"
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

	// pace — темп запросов, общий для клиента и всех его клонов.
	//
	// Указатель, а не поле: клоны (withCookies, withoutAcceptLang) копируют
	// структуру целиком, и своим полем каждый получил бы отдельную квоту к тому
	// же хосту — то есть троттлинг перестал бы работать ровно там, где площадка
	// требует cookie. Указатель делят все копии, и замок внутри копируется как
	// адрес, а не как значение.
	pace *pacer
}

func newClient(timeoutSec, retries int) *Client {
	return &Client{
		http:       &http.Client{Timeout: time.Duration(timeoutSec) * time.Second},
		retries:    retries,
		userAgent:  browserUA,
		acceptLang: "ru-RU,ru;q=0.9,en;q=0.8",
		pace:       newPacer(),
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

// withCookies — тот же клиент, но с банкой cookie.
//
// Отличается от newSessionClient тем, что СОХРАНЯЕТ транспорт исходного
// клиента: у туннельного там прокси, и создание банки с нуля увело бы запросы
// мимо российского выхода — ровно та ошибка, которая выглядит как «туннель не
// работает».
//
// Живой случай: luxorfilm.ru отдаёт площадке «Весна» бесконечную цепочку
// редиректов, пока в куке не сохранён выбор кинотеатра; «Гудзон» при этом
// отвечает и без банки. Без cookie Весна выглядела бы сломанным источником.
func (c *Client) withCookies() *Client {
	clone := *c
	inner := *c.http
	if jar, err := cookiejar.New(nil); err == nil {
		inner.Jar = jar
	}
	clone.http = &inner
	return &clone
}

// newNoRedirectClient — клиент, который переход по 30x не выполняет, а отдаёт
// сам код ответа.
//
// Нужен там, где редирект — это ОТВЕТ источника, а не техническая пересылка.
// Живой случай: kinoteatr.ru на дату, которой у площадки нет в расписании,
// отвечает 301 на страницу-обёртку с ближайшим доступным днём. Клиент по
// умолчанию переход выполняет, и разбор получает HTML вместо JSON — то есть
// пустой день выглядит сломанным каналом. Проверено на двух площадках:
// mosfilm на сегодня и kaluzhskij на 31.12 отвечают одинаково, а на даты со
// своими сеансами обе отдают JSON.
func newNoRedirectClient(timeoutSec, retries int) *Client {
	c := newClient(timeoutSec, retries)
	c.http.CheckRedirect = func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
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

// throttle ждёт очереди к хосту и возвращает функцию освобождения.
//
// Темп живёт в pacer, общем для клиента и его клонов: обход площадок идёт
// параллельно, и состояние темпа полем структуры было бы гонкой.
func (c *Client) throttle(host string) func() {
	if c.pace == nil {
		c.pace = newPacer()
	}
	return c.pace.acquire(host, c.minInterval)
}

// requestHost — хост из адреса запроса. Он же ключ очереди к кассе.
//
// Адрес битый — возвращается сам адрес: тогда очередь у него своя, и это лучше
// общей на все нечитаемые адреса.
func requestHost(addr string) string {
	u, err := url.Parse(addr)
	if err != nil || u.Host == "" {
		return addr
	}
	return u.Host
}

// retryAfter — сколько ждать по просьбе кассы.
//
// Заголовок бывает в двух видах, и оба законны: число секунд и дата. Пустой,
// битый или отрицательный — берётся своя лесенка повторов, потому что «касса не
// сказала» не означает «можно сразу».
func retryAfter(header string, fallback time.Duration) time.Duration {
	header = strings.TrimSpace(header)
	if header == "" {
		return fallback
	}
	if secs, err := strconv.Atoi(header); err == nil {
		if d := time.Duration(secs) * time.Second; d > 0 {
			return d
		}
		return fallback
	}
	if until, err := http.ParseTime(header); err == nil {
		if d := time.Until(until); d > 0 {
			return d
		}
	}
	return fallback
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

// postJSON — POST с телом JSON и дополнительными заголовками.
//
// Нужен ровно одному источнику, но обойтись без него нельзя: у Романова Синема
// серверный HTML — пустой шаблон (фильм «test», времена «00:00»), а настоящее
// расписание отдаёт POST-ручка. Разбор шаблона дал бы непустую афишу из мусора,
// то есть площадка выглядела бы рабочей и отдавала выдуманные сеансы.
func (c *Client) postJSON(url, body string, extra map[string]string) (string, int, error) {
	head := map[string]string{"content-type": "application/json"}
	for k, v := range extra {
		head[k] = v
	}
	return c.do(http.MethodPost, url, body, head)
}

// getHeaders — тот же запрос с дополнительными заголовками.
//
// Нужны кассам, где площадка задаётся не адресом: Kinoplan различает
// приложения по x-application-token и без x-platform отвечает 400.
func (c *Client) getHeaders(url string, extra map[string]string) (string, int, error) {
	return c.do(http.MethodGet, url, "", extra)
}

// do — общий транспорт с ретраями. Метод и тело параметрами, чтобы POST-ручка
// получала ту же политику повторов, что и все остальные запросы.
func (c *Client) do(method, addr, body string, extra map[string]string) (string, int, error) {
	var backoff time.Duration
	netAttempt, rlAttempt := 0, 0
	host := requestHost(addr)

	for {
		if backoff > 0 {
			time.Sleep(backoff)
			backoff = 0
		}
		// Очередь к кассе берётся на один запрос и отпускается сразу после
		// ответа: паузы повторов ждём БЕЗ слота, иначе соседние площадки той же
		// сети простаивают вместе с нами.
		release := c.throttle(host)

		ctx, cancel := context.WithTimeout(context.Background(), c.http.Timeout)
		var reader io.Reader
		if body != "" {
			reader = strings.NewReader(body)
		}
		req, err := http.NewRequestWithContext(ctx, method, addr, reader)
		if err != nil {
			cancel()
			release()
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
			release()
			if netAttempt < c.retries {
				backoff = transientBackoff(netAttempt)
				netAttempt++
				continue
			}
			return "", 0, fmt.Errorf("сеть: %w", err)
		}

		respBody, readErr := io.ReadAll(resp.Body)
		resp.Body.Close()
		cancel()
		release()

		switch {
		case resp.StatusCode == http.StatusTooManyRequests:
			// Отказ по частоте — свойство кассы, а не одного запроса: пауза
			// ставится на весь хост, и её увидят соседние площадки той же сети.
			wait := retryAfter(resp.Header.Get("Retry-After"), rateLimitBackoff(rlAttempt))
			c.pace.penalize(host, wait)
			if rlAttempt < c.retries+2 {
				backoff = wait
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
		return string(respBody), resp.StatusCode, nil
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
