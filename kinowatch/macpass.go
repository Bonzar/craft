package main

// Пропуск через настоящий браузер на маке.
//
// Зачем. Кинoафиша закрывает доступ полосами: 403 на любой запрос, независимо от
// заголовков, протокола и клиента. Три захода объяснить это чем-то в запросе
// провалились — полоса приходит и уходит сама, а замеры описывали разные её
// фазы. Настоящий браузер её проходит.
//
// Как. Браузер работает ПРОПУСКОМ, а не вторым разбором: он открывает сайт
// обычной навигацией, а дальше запросы идут ИЗНУТРИ уже открытой страницы. Так
// ответ приходит сырым серверным HTML, побайтово как из сети (замер 12.08.2026:
// страница фильма 672646 байт, маркеры в исходной форме `data-schedule-next='{…}'`;
// выдача поиска 204885 байт, разбирается существующим выражением). Чтение
// готовой страницы браузера даёт другое — там кавычки экранированы одинарно, а
// амперсанд двойным, и наши выражения такую страницу не читают.
//
// Отсюда правило: наверх уходит тело и код ответа, ровно как у сетевого
// клиента, и выше уровня транспорта разницы между путями нет.

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

// Настройки ступени на прогон. Пакетные переменные, а не поля клиента: ступень
// нужна одному слою из десятка, и тащить её через все подписи значило бы
// размазать частный случай по всему инструменту.
var (
	macPassMode    = passAuto
	macHelmPath    string
	macTunnelPath  string
	macPassTimeout = 15 * time.Minute
)

// repoRoot — корень репозитория, от которого ищутся скрипты контура.
//
// Считается от рабочего каталога: инструмент запускают либо из корня, либо из
// своей папки. Не нашли — пусть пути задаются ручками, это честнее догадок.
func repoRoot() string {
	wd, err := os.Getwd()
	if err != nil {
		return "."
	}
	for dir := wd; ; {
		if _, err := os.Stat(filepath.Join(dir, "tools", "mac", "h.sh")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return wd
		}
		dir = parent
	}
}

// rawRequest — один запрос, который надо выполнить на стороне источника.
type rawRequest struct {
	URL     string            `json:"url"`
	Method  string            `json:"method,omitempty"`
	Headers map[string]string `json:"headers,omitempty"`
}

// rawResponse — тело и код ответа. Err непустая означает, что до ответа дело не
// дошло вовсе: путать «источник ответил отказом» и «запрос не состоялся»
// нельзя, диагноз у них разный.
type rawResponse struct {
	URL    string `json:"url"`
	Status int    `json:"status"`
	Body   string `json:"body"`
	Err    string `json:"err,omitempty"`
}

// bodyFetcher — способ выполнить пачку запросов.
//
// Пачкой, а не по одному, ради ступени мака: каждый заход туда стоит связи с
// ноутом через реле, и три десятка заходов вместо четырёх превратили бы прогон
// в получасовой. Сетевой путь ту же пачку просто проходит циклом.
type bodyFetcher interface {
	fetch(reqs []rawRequest) ([]rawResponse, error)
}

// netFetcher — обычный путь: тот же клиент, что у всех остальных каналов.
type netFetcher struct{ c *Client }

func (f netFetcher) fetch(reqs []rawRequest) ([]rawResponse, error) {
	out := make([]rawResponse, 0, len(reqs))
	for _, r := range reqs {
		method := r.Method
		if method == "" {
			method = "GET"
		}
		body, status, err := f.c.do(method, r.URL, "", r.Headers)
		res := rawResponse{URL: r.URL, Status: status, Body: body}
		if err != nil {
			res.Err = err.Error()
		}
		out = append(out, res)
	}
	return out, nil
}

// ——— ступень мака ———

const (
	// macPassMarker — по нему тела отделяются от вывода пульта. Первая строка не
	// годится: пульт печатает и своё, и чужое. Замер 12.08.2026 — одиннадцать
	// первых строк заняты трейсом внутренней ошибки инструмента браузера, и
	// данные начинались двенадцатой.
	macPassMarker = "KINOWATCH_PASS "

	// macPassHelpMarker — зов о помощи от капчи. Пульт печатает его в тот же
	// поток, а мы показываем человеку: сетевого канала из песочницы нет.
	macPassHelpMarker = "ЧЕЛОВЕК НУЖЕН:"
)

// macPass — транспорт через пульт репозитория.
//
// Своего канала связи не заводит: и подъём канала до ноута, и сам заход в
// браузер делают скрипты репозитория. Здесь только сборка задания, запуск и
// разбор вывода.
type macPass struct {
	helm    string        // пульт: tools/mac/h.sh
	script  string        // скрипт пропуска: tools/mac/fetch-raw.js
	tunnel  string        // подъём канала: tools/mac-tunnel.sh
	node    string        // tailnet-адрес узла с браузером
	timeout time.Duration // предел ожидания одного захода

	once sync.Once
	err  error
}

// newMacPass собирает ступень из путей репозитория.
func newMacPass(root, helm, tunnel string, timeout time.Duration) *macPass {
	if helm == "" {
		helm = filepath.Join(root, "tools", "mac", "h.sh")
	}
	if tunnel == "" {
		tunnel = filepath.Join(root, "tools", "mac-tunnel.sh")
	}
	return &macPass{
		helm:    helm,
		script:  filepath.Join(filepath.Dir(helm), "fetch-raw.js"),
		tunnel:  tunnel,
		node:    strings.TrimSpace(os.Getenv("MAC_NODE_ADDR")),
		timeout: timeout,
	}
}

// available отвечает, можно ли вообще звать ступень.
//
// Признак не «это мак», а совпадение с целевым узлом: инструмент законно
// работает на другой машине под macOS, и там ступень как раз нужна. Молчит она
// только на самом узле с браузером — туда ходить неоткуда, а подъём канала там
// переконфигурировал бы тот демон, на котором канал и держится.
func (m *macPass) available() error {
	if m.node == "" {
		return fmt.Errorf("ступень мака недоступна: в окружении нет MAC_NODE_ADDR")
	}
	if runningOnNode(m.node) {
		return fmt.Errorf("ступень мака не поднимается на самом узле %q: ходить некуда", m.node)
	}
	if _, err := os.Stat(m.helm); err != nil {
		return fmt.Errorf("ступень мака недоступна: нет пульта %s", m.helm)
	}
	if _, err := os.Stat(m.script); err != nil {
		return fmt.Errorf("ступень мака недоступна: нет скрипта пропуска %s", m.script)
	}
	return nil
}

// runningOnNode — та ли это машина, к которой мы собираемся ходить.
//
// Сравниваются первые метки имён: у узла оно вида «имя.tailnet.ts.net», а
// hostname машины короткий. Точнее сравнить нечем и не нужно — совпадения имени
// достаточно, чтобы не ходить к самому себе.
func runningOnNode(node string) bool {
	host, err := os.Hostname()
	if err != nil || host == "" {
		return false
	}
	return strings.EqualFold(firstLabel(host), firstLabel(node))
}

func firstLabel(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.Index(s, "."); i >= 0 {
		s = s[:i]
	}
	return strings.ToLower(s)
}

// ensureTunnel поднимает канал до ноута перед первым заходом.
//
// Скрипт идемпотентен: живой демон он переиспользует и лишь перепроверяет
// выход, поэтому звать его на каждый прогон безопасно. Один раз на ступень —
// потому что второй заход ничего не добавит.
func (m *macPass) ensureTunnel() error {
	m.once.Do(func() {
		if _, err := os.Stat(m.tunnel); err != nil {
			m.err = fmt.Errorf("нет скрипта канала %s", m.tunnel)
			return
		}
		cmd := exec.Command("bash", m.tunnel)
		cmd.Stdout, cmd.Stderr = os.Stderr, os.Stderr
		if err := cmd.Run(); err != nil {
			m.err = fmt.Errorf("канал до мака не поднялся: %w", err)
		}
	})
	return m.err
}

// fetch выполняет пачку запросов через браузер на маке.
func (m *macPass) fetch(reqs []rawRequest) ([]rawResponse, error) {
	if err := m.available(); err != nil {
		return nil, err
	}
	if err := m.ensureTunnel(); err != nil {
		return nil, err
	}
	if len(reqs) == 0 {
		return nil, nil
	}

	job, err := m.jobFile(reqs)
	if err != nil {
		return nil, err
	}
	defer os.Remove(job)

	// Предел ожидания живёт здесь, а не в пульте: пульт намеренно работает без
	// срока, потому что срок убивал бы ожидание человека у капчи.
	secs := int(m.timeout / time.Second)
	if secs < 60 {
		secs = 60
	}
	cmd := exec.Command("bash", m.helm, job, fmt.Sprintf("%d", secs))
	cmd.Stderr = os.Stderr
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("пропуск через мак не отработал: %w", err)
	}
	return parseMacPassOutput(string(out))
}

// jobFile складывает задание и скрипт пропуска в одну программу.
//
// Задание уезжает объявлением перед скриптом, а не аргументом: пульт шлёт
// программу целиком и аргументов ей не передаёт. Скрипт от этого остаётся
// свободным от знания про источник — он исполняет то, что ему назвали.
func (m *macPass) jobFile(reqs []rawRequest) (string, error) {
	body, err := os.ReadFile(m.script)
	if err != nil {
		return "", fmt.Errorf("скрипт пропуска не читается: %w", err)
	}
	// Адрес навигации — первый запрос пачки: именно им проходится антибот, и
	// изнутри этой страницы выполняются остальные.
	task, err := json.Marshal(struct {
		Nav      string       `json:"nav"`
		Requests []rawRequest `json:"requests"`
	}{Nav: reqs[0].URL, Requests: reqs})
	if err != nil {
		return "", err
	}

	f, err := os.CreateTemp("", "kinowatch-pass-*.js")
	if err != nil {
		return "", err
	}
	defer f.Close()
	if _, err := fmt.Fprintf(f, "const JOB = %s;\n%s", task, body); err != nil {
		return "", err
	}
	return f.Name(), nil
}

// parseMacPassOutput достаёт тела из вывода пульта.
func parseMacPassOutput(out string) ([]rawResponse, error) {
	for _, line := range strings.Split(out, "\n") {
		if strings.HasPrefix(line, macPassHelpMarker) {
			// Человека зовут прямо сейчас — строку показываем, иначе разовая
			// ссылка на проверку до него не доедет.
			fmt.Fprintln(os.Stderr, line)
			continue
		}
		if !strings.HasPrefix(line, macPassMarker) {
			continue
		}
		var res struct {
			Responses []rawResponse `json:"responses"`
			Notes     []string      `json:"notes"`
		}
		if err := json.Unmarshal([]byte(strings.TrimPrefix(line, macPassMarker)), &res); err != nil {
			return nil, fmt.Errorf("ответ пропуска не читается: %w", err)
		}
		for _, n := range res.Notes {
			fmt.Fprintln(os.Stderr, "пропуск: "+n)
		}
		return res.Responses, nil
	}
	return nil, fmt.Errorf("в выводе пропуска нет ответов (маркер %q не встретился)", strings.TrimSpace(macPassMarker))
}
