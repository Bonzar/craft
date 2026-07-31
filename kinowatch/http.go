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
	"strings"
	"time"
)

// browserUA — честный десктопный User-Agent.
// Не маскировка: часть сайтов режет запросы без UA как ботов, при этом мы не
// притворяемся человеком в интерфейсе и не обходим капчу.
const browserUA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
	"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

type Client struct {
	http    *http.Client
	retries int
}

func newClient(timeoutSec, retries int) *Client {
	return &Client{
		http:    &http.Client{Timeout: time.Duration(timeoutSec) * time.Second},
		retries: retries,
	}
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
	var backoff time.Duration
	netAttempt, rlAttempt := 0, 0

	for {
		if backoff > 0 {
			time.Sleep(backoff)
			backoff = 0
		}

		ctx, cancel := context.WithTimeout(context.Background(), c.http.Timeout)
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			cancel()
			return "", fmt.Errorf("построение запроса: %w", err)
		}
		req.Header.Set("User-Agent", browserUA)
		req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
		req.Header.Set("Accept-Language", "ru-RU,ru;q=0.9,en;q=0.8")

		resp, err := c.http.Do(req)
		if err != nil {
			cancel()
			if netAttempt < c.retries {
				backoff = transientBackoff(netAttempt)
				netAttempt++
				continue
			}
			return "", fmt.Errorf("сеть: %w", err)
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
			return "", fmt.Errorf("HTTP 429 после ретраев")

		case resp.StatusCode >= 500:
			if netAttempt < c.retries {
				backoff = transientBackoff(netAttempt)
				netAttempt++
				continue
			}
			return "", fmt.Errorf("HTTP %d после ретраев", resp.StatusCode)

		case resp.StatusCode >= 400:
			return "", fmt.Errorf("HTTP %d", resp.StatusCode)
		}

		if readErr != nil {
			return "", fmt.Errorf("чтение тела: %w", readErr)
		}
		return string(body), nil
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
