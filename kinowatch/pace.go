package main

// Темп запросов: параллельность между кассами, но не внутри одной.
//
// Обход площадок идёт пулом воркеров, и без ограничителя восемь воркеров легко
// оказываются восемью одновременными запросами к ОДНОЙ кассе: у «Москино» в
// реестре двадцать площадок на одном сервере, у «Синема Парк» пятнадцать.
// Ускорения это не даёт — узкое место всё равно сервер кассы, — зато выглядит
// как атака.
//
// Отсюда правило: ключ ограничения — ХОСТ, а не вид канала. У kinoplan, p24 и
// «Премьер-зала» площадки живут на своих доменах, и вид их не различает.
//
// Второе, что делает pacer, — помнит отказ по частоте. Ответ 429 сегодня
// отодвигает только тот запрос, который его получил; соседние к той же кассе
// продолжают идти прежним темпом и добивают её. Пауза после 429 ставится на весь
// хост, и зазор между запросами к нему остаётся до конца прогона: касса,
// однажды сказавшая «слишком часто», второй раз скажет это быстрее.

import (
	"sync"
	"time"
)

// paceAfterLimit — зазор между запросами к хосту, который ответил 429.
//
// Не возвращаем прежний темп сразу после паузы: лимит — свойство кассы, а не
// одной неудачной секунды.
const paceAfterLimit = 2 * time.Second

// pacer — темп запросов клиента и всех его клонов.
type pacer struct {
	mu    sync.Mutex
	last  time.Time // общий темп клиента, для minInterval
	hosts map[string]*hostPace
}

// hostPace — состояние одного хоста.
type hostPace struct {
	// busy пропускает к хосту ровно один запрос за раз.
	busy chan struct{}

	mu sync.Mutex
	// notBefore — раньше этого времени к хосту не ходим: касса ответила 429.
	notBefore time.Time
	// gap — минимальный зазор между запросами. Ноль, пока касса не жаловалась.
	gap time.Duration
	// last — когда к хосту ходили в прошлый раз.
	last time.Time
}

func newPacer() *pacer { return &pacer{hosts: map[string]*hostPace{}} }

func (p *pacer) host(name string) *hostPace {
	p.mu.Lock()
	defer p.mu.Unlock()
	hp, ok := p.hosts[name]
	if !ok {
		hp = &hostPace{busy: make(chan struct{}, 1)}
		p.hosts[name] = hp
	}
	return hp
}

// acquire ждёт своей очереди к хосту и возвращает функцию освобождения.
//
// Ожидание бывает двух родов: очередь за другим запросом к той же кассе и пауза,
// назначенная её же ответом 429. Оба ждутся здесь, а не в вызывающем коде, чтобы
// про темп не пришлось помнить на каждой ручке.
func (p *pacer) acquire(host string, minInterval time.Duration) func() {
	hp := p.host(host)
	hp.busy <- struct{}{}

	hp.mu.Lock()
	wait := time.Until(hp.notBefore)
	if hp.gap > 0 && !hp.last.IsZero() {
		if d := hp.gap - time.Since(hp.last); d > wait {
			wait = d
		}
	}
	hp.mu.Unlock()

	if minInterval > 0 {
		p.mu.Lock()
		if !p.last.IsZero() {
			if d := minInterval - time.Since(p.last); d > wait {
				wait = d
			}
		}
		p.last = time.Now().Add(maxDuration(wait, 0))
		p.mu.Unlock()
	}

	if wait > 0 {
		time.Sleep(wait)
	}

	return func() {
		hp.mu.Lock()
		hp.last = time.Now()
		hp.mu.Unlock()
		<-hp.busy
	}
}

// penalize отодвигает ВСЕ запросы к хосту после его отказа по частоте.
func (p *pacer) penalize(host string, d time.Duration) {
	if d <= 0 {
		return
	}
	hp := p.host(host)
	hp.mu.Lock()
	defer hp.mu.Unlock()

	if until := time.Now().Add(d); until.After(hp.notBefore) {
		hp.notBefore = until
	}
	if hp.gap < paceAfterLimit {
		hp.gap = paceAfterLimit
	}
}

func maxDuration(a, b time.Duration) time.Duration {
	if a > b {
		return a
	}
	return b
}
