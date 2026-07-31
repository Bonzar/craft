package main

// Классификация результата опроса площадки.
//
// Здесь живёт главное различие всего инструмента: «источник сломался» — это НЕ
// «фильма нет». Смешать их значит однажды спокойно отрапортовать «билетов нигде
// нет», когда на самом деле половина касс молчала.
//
// Поэтому статус `absent` — единственный, означающий «фильма действительно нет»,
// и выставляется он только при ДОКАЗАННОЙ живости источника. Доказательство
// всегда положительное: что-то в ответе есть. Отсутствие ошибки живостью не
// считается — HTTP 200 с пустым телом отдают и сломанные эндпоинты.

import (
	"strconv"
	"strings"
	"time"
)

// Статусы последнего прогона. Живут в колонке LastStatus и меняются каждый час
// — в отличие от StatusClass, который описывает саму площадку.
const (
	statusOnSale = "on_sale" // фильм найден, билеты продаются
	statusFound  = "found"   // фильм найден, но продажи ещё не открыты
	statusAbsent = "absent"  // источник жив и фильма у него нет

	statusSuspect = "source_suspect" // ответ пришёл, но пустой или протухший
	statusStale   = "stale"          // площадка молчит несколько прогонов подряд

	statusBrokenAuth        = "source_broken:auth"
	statusBrokenParse       = "source_broken:parse"
	statusBrokenUnreachable = "source_broken:unreachable"
)

// ProbeInput — всё, что известно о попытке опроса одной площадки.
//
// Отдельная структура, а не набор аргументов: классификация обязана быть чистой
// функцией, которую целиком накрывает табличный тест. Решение «источник жив»
// слишком дорогое, чтобы принимать его по ходу сетевого кода.
type ProbeInput struct {
	// Транспорт. Err непустая — сеть или HTTP не удались.
	Err        error
	HTTPStatus int
	BodySize   int

	// ParseErr — разбор не смог найти в теле свою разметку. Это отдельный вид
	// поломки: тело пришло, а структура сменилась.
	ParseErr error

	// Что дал разбор. Showtimes — ВСЯ афиша площадки, а не только искомый фильм:
	// живость доказывается чужими сеансами не хуже своих.
	Playbill Playbill

	// Найден ли искомый фильм и продаются ли на него билеты.
	FilmFound  bool
	FilmOnSale bool

	// Now — опорный момент. Параметром, а не time.Now() внутри: иначе тест
	// «максимальная дата сеанса в прошлом» зависел бы от дня запуска.
	Now time.Time
}

// ProbeResult — статус и его обоснование.
//
// Evidence отвечает на вопрос «почему так решили» и уезжает в реестр: без него
// разбор чужого прогона превращается в гадание.
type ProbeResult struct {
	Status   string
	Evidence string
	// Alive — доказана ли живость источника. Отдельно от статуса, потому что
	// живой источник бывает и с absent, и с on_sale.
	Alive bool
}

// classifyProbe решает, что означает ответ площадки.
//
// Порядок проверок идёт от самого надёжного объяснения к самому шаткому:
// сначала транспорт (ответа не было вовсе), потом разбор (тело есть, структуры
// нет), и только потом содержимое.
func classifyProbe(in ProbeInput) ProbeResult {
	// 1. Транспорт. Отдельно 401/403/404 у источников с токеном: у Kinoplan
	// протухшее приложение отвечает 404 «App not found», и лечится это заменой
	// токена, а не ретраем.
	if in.Err != nil {
		return ProbeResult{Status: statusBrokenUnreachable, Evidence: in.Err.Error()}
	}
	switch {
	case in.HTTPStatus == 401 || in.HTTPStatus == 403 || in.HTTPStatus == 404:
		return ProbeResult{Status: statusBrokenAuth, Evidence: httpEvidence(in.HTTPStatus)}
	case in.HTTPStatus >= 400:
		return ProbeResult{Status: statusBrokenUnreachable, Evidence: httpEvidence(in.HTTPStatus)}
	case in.HTTPStatus != 200 && in.HTTPStatus != 0:
		return ProbeResult{Status: statusSuspect, Evidence: httpEvidence(in.HTTPStatus)}
	}

	// 2. Разбор. Ноль позиций при большом теле — почти наверняка сменившаяся
	// вёрстка, и признать это «фильма нет» было бы худшей из ошибок.
	if in.ParseErr != nil {
		return ProbeResult{Status: statusBrokenParse, Evidence: in.ParseErr.Error()}
	}

	// 3. Содержимое. Пустая афиша при HTTP 200 живостью не является.
	if len(in.Playbill.Showtimes) == 0 {
		return ProbeResult{
			Status:   statusSuspect,
			Evidence: "HTTP 200, но афиша пуста: " + sizeEvidence(in.BodySize),
		}
	}

	// 4. Протухшее расписание: сеансы есть, но все в прошлом. Так выглядит
	// брошенная страница, которую никто не обновляет.
	if last := maxStart(in.Playbill); !last.IsZero() && last.Before(in.Now) {
		return ProbeResult{
			Status:   statusSuspect,
			Evidence: "последний сеанс " + last.Format(time.RFC3339) + " уже в прошлом",
		}
	}

	// Живость доказана: источник отдал непустую и неустаревшую афишу.
	switch {
	case in.FilmFound && in.FilmOnSale:
		return ProbeResult{Status: statusOnSale, Alive: true, Evidence: playbillEvidence(in.Playbill)}
	case in.FilmFound:
		return ProbeResult{Status: statusFound, Alive: true, Evidence: playbillEvidence(in.Playbill)}
	default:
		return ProbeResult{Status: statusAbsent, Alive: true, Evidence: playbillEvidence(in.Playbill)}
	}
}

// maxStart — самое позднее время сеанса в афише.
func maxStart(pb Playbill) time.Time {
	var out time.Time
	for _, s := range pb.Showtimes {
		t, err := time.Parse(time.RFC3339, s.StartsAt)
		if err != nil {
			continue
		}
		if t.After(out) {
			out = t
		}
	}
	return out
}

func httpEvidence(code int) string {
	return "HTTP " + strconv.Itoa(code)
}

func sizeEvidence(size int) string {
	return "тело " + strconv.Itoa(size) + " байт"
}

func playbillEvidence(pb Playbill) string {
	films := map[string]bool{}
	for _, s := range pb.Showtimes {
		if s.Film != "" {
			films[s.Film] = true
		}
	}
	return "сеансов " + strconv.Itoa(len(pb.Showtimes)) + ", фильмов " + strconv.Itoa(len(films))
}

// markStale помечает площадку, молчащую несколько прогонов подряд.
//
// Идёт в LastStatus, а не в StatusClass: молчание источника — это результат
// прогона, а не свойство площадки. Площадка при этом остаётся видимой, а не
// исчезает из отчёта.
const staleAfterFailures = 3

func markStale(consecutiveFailures int, current string) string {
	if consecutiveFailures >= staleAfterFailures && strings.HasPrefix(current, "source_") {
		return statusStale
	}
	return current
}
