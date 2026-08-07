package main

// Сборка запроса к каналу площадки.
//
// Недостающее звено всей цепочки: адаптеры умеют разобрать ответ кассы, но
// сами по себе не знают, куда стучаться. Здесь по виду канала и идентификатору
// площадки собирается запрос, ответ уходит своему адаптеру, а наружу выходит
// афиша вместе со всем, что нужно классификатору исхода.
//
// Формы запроса добыты живьём и проверены на настоящих ответах. Два адреса
// перебором не находились: у КАРО путь виден только по запросам собственного
// сайта, и вытащен браузером.
//
// Главный принцип тот же, что у классификатора: «не смогли спросить» никогда не
// должно выглядеть как «фильма нет». Поэтому неизвестный вид канала — ошибка, а
// не пустая афиша, и день, который канал не отдал, помечается отдельно.

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// ChannelProbe — сырой итог обращения к каналу одной площадки.
//
// Повторяет поля ProbeInput не случайно: это и есть его транспортная часть,
// собранная в одном месте, чтобы решение о живости источника принималось
// чистой функцией, а не по ходу сетевого кода.
type ChannelProbe struct {
	Playbill Playbill
	Status   int
	BodySize int
	Err      error
	ParseErr error

	// FailedDays — даты, за которые канал ответа не дал, хотя за другие дал.
	//
	// Отдельно от Err: источник, ответивший за половину горизонта, жив, и
	// объявлять его сломанным неверно. Но и полным его ответ не назовёшь —
	// вывод «фильма нет» по неполному горизонту делать нельзя, и решает это
	// вызывающий, глядя на этот список.
	FailedDays []string

	// WindowFrom и WindowTo — ФАКТИЧЕСКОЕ окно источника: первая и последняя
	// даты, на которые он дал хоть один сеанс любого фильма.
	//
	// Нужно потому, что запрошенное окно и покрытое источником — разные вещи, и
	// расходятся они у обоих типов каналов. Канал, отдающий окно целиком,
	// упирается в свой край сразу: КАРО присылает 23 даты, сколько ни проси.
	// Канал, обходимый по дню, упирается в него дальними датами: они приходят
	// успешным ответом с пустым репертуаром.
	//
	// Про даты вне этого окна источник не сказал НИЧЕГО, и «фильма там нет» —
	// утверждение без основания. Дырка внутри окна — другое дело: там площадка
	// честно не работает в этот день.
	//
	// Пустые обе строки означают, что сеансов не пришло вовсе. Это уже отдельный
	// исход («пустая афиша»), и решает его классификатор, а не это поле.
	WindowFrom string
	WindowTo   string

	// BodyHash — отпечаток тела ответа, по которому видно, что источник
	// запрошенную дату не различил: два дня подряд с одинаковым телом означают,
	// что он отдал одну и ту же страницу.
	//
	// Пустой означает «сравнивать нечем», а не «тела совпали»: часть каналов
	// собирает ответ мимо общего скелета запроса и отпечатка не ставит. Все
	// такие каналы несут дату в самом запросе, поэтому потеря чека для них
	// безопасна.
	BodyHash string

	// DateBlind непустая означает, что источник отдал на разные даты один и тот
	// же ответ. Не ошибка канала: он жив и отвечает, просто про запрошенные дни
	// ничего не сказал — и приписывать им сеансы первого дня не на чем.
	DateBlind string
}

// sourceWindow — фактическое окно афиши: от первой даты сеанса до последней.
func sourceWindow(pb Playbill) (string, string) {
	var lo, hi string
	for _, s := range pb.Showtimes {
		if len(s.StartsAt) < 10 {
			continue
		}
		day := s.StartsAt[:10]
		if lo == "" || day < lo {
			lo = day
		}
		if hi == "" || day > hi {
			hi = day
		}
	}
	return lo, hi
}

// uncoveredDates — запрошенные даты, которых фактическое окно источника не
// накрывает.
//
// Считаются только КРАЯ: всё до начала окна и всё после его конца. Дни внутри
// окна не считаются непокрытыми, даже если сеансов в них нет, — площадка имеет
// право не работать, и это честное «фильма нет в этот день».
func uncoveredDates(from time.Time, days int, winFrom, winTo string) []string {
	if winFrom == "" || winTo == "" || days < 1 {
		return nil
	}
	var out []string
	for i := 0; i < days; i++ {
		day := from.AddDate(0, 0, i).Format("2006-01-02")
		if day < winFrom || day > winTo {
			out = append(out, day)
		}
	}
	return out
}

// ChannelParams — то, что нужно каналу сверх его вида, чтобы попасть в нужную
// площадку.
//
// Одного идентификатора хватает не всем: p24 держит площадки на РАЗНЫХ доменах,
// Kinoplan различает их номером виджета, из которого выводится токен, Pushka —
// кукой сессии. Поэтому параметры хранятся набором «ключ=значение», а не одной
// строкой.
type ChannelParams map[string]string

// Ключи параметров канала.
const (
	pVenue = "venue" // идентификатор площадки в её канале
	pHost  = "host"  // собственный домен площадки, если движок общий
)

// parseChannelParams разбирает поле SourceParams реестра.
//
// Старая форма «venue=<id>» — частный случай новой и читается как раньше:
// строки, записанные прошлыми прогонами, остаются рабочими.
//
// Форма «leader=<ключ>» у клонов живёт в этом же поле и разбирается сюда же
// без вреда: клоны в опрос не идут, а ключ остаётся видимым в отчёте.
func parseChannelParams(s string) ChannelParams {
	out := ChannelParams{}
	for _, part := range strings.Split(s, ";") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		k, v, ok := strings.Cut(part, "=")
		if !ok {
			continue
		}
		out[strings.TrimSpace(k)] = strings.TrimSpace(v)
	}
	return out
}

// String собирает параметры обратно в поле реестра, порядок ключей устойчив.
func (p ChannelParams) String() string {
	keys := make([]string, 0, len(p))
	for k := range p {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+p[k])
	}
	return strings.Join(parts, ";")
}

// channelWindowWhole — каналы, отдающие весь свой горизонт одним ответом.
//
// Замерено живьём: КАРО отдал 14 дат до конца месяца, Синема Стар — 10, Москино
// — 6. Остальные три отвечают строго за одну дату, и горизонт им набирается
// запросом на день.
var channelWindowWhole = map[string]bool{
	kindKaro:       true,
	kindCinemaStar: true,
	kindMoskino:    true,
	// Синема 5 сюда попадает по другой причине, чем остальные три: не потому,
	// что отдаёт много дат одним ответом, а потому, что БОЛЬШЕ ДВУХ у него нет
	// вовсе. Страницы today и tomorrow отвечают, произвольная дата в пути —
	// пустым списком, soon и all не существуют. Обход по дню накопил бы пять
	// пустых дней и объявил живой канал дырявым, хотя дыра — свойство самого
	// источника.
	kindCinema5: true,
	// Поклонка отдаёт все свои дни одной страницей: дни — блоки внутри неё,
	// параметра даты у источника нет вовсе.
	kindPoklonka: true,
	// Иллюзион отдаёт все дни одной страницей, параметра даты у него нет.
	kindIllusion: true,
	// Третьяковка отдаёт все свои показы одной страницей, дат в адресе нет.
	kindTretyakov: true,
	// Еврейский музей публикует всю афишу одной страницей, дат в адресе нет.
	kindJewish: true,
	// Pushka отдаёт полное расписание площадки: параметра даты у запроса нет
	// вовсе. Пока её здесь не было, обход по дню звал один и тот же ответ по
	// разу на день и складывал его сам с собой — на двухдневном горизонте это
	// давало ровно двойной комплект сеансов.
	kindPushka: true,
	// Премьер-зал публикует ТОЛЬКО сегодня, и взять у него другой день нечем:
	// форма даты расписание не меняет (три разные даты дали байт в байт одну
	// страницу), а pjax-фрагмент отвечает «сеансов нет» на любую дату, включая
	// сегодняшнюю. Пока канал обходился по дню, сегодняшние сеансы уезжали в
	// отчёт под всеми 28 датами окна.
	//
	// Правильность держится на том, что прогон стартует с сегодня — другой
	// начальной даты у инструмента нет. Появится ключ начальной даты — этот
	// канал придётся пересматривать первым: он проставит сеансам чужой день.
	kindPremierzal: true,
	// Алмаз отдаёт одну и ту же страницу на любой запрос, но датирует сеансы
	// сам — данные верные, лишними были только 27 запросов из 28.
	kindAlmaz: true,
}

// fetchChannel опрашивает площадку на горизонт в days дней от from.
//
// Канал, отдающий окно целиком, спрашивается один раз — лишние запросы к чужой
// кассе тут ничего не добавляют. Остальные обходятся по дню.
func fetchChannel(c *Client, kind string, p ChannelParams, from time.Time, days int) ChannelProbe {
	if days < 1 {
		days = 1
	}
	if channelWindowWhole[kind] {
		one := fetchChannelDay(c, kind, p, from)
		one.WindowFrom, one.WindowTo = sourceWindow(one.Playbill)
		return one
	}

	plan, inHorizon := horizonPlan(from, days)

	var out ChannelProbe
	var lastFail ChannelProbe
	got := 0
	seen := map[string]bool{}
	seenDates := map[string]bool{}
	narrowed := false
	bodies := map[string]bodySeen{}

	for i := 0; i < len(plan); i++ {
		day := plan[i]
		date := day.Format("2006-01-02")
		one := fetchChannelDay(c, kind, p, day)

		// Источник ответил, но страница не про этот день. Не отказ: канал жив,
		// просто этот день он не публикует — и в список неответивших он не
		// попадает, иначе живой источник объявлялся бы дырявым.
		if errors.Is(one.ParseErr, errDayNotPublished) {
			if !narrowed && len(one.Playbill.Dates) > 0 {
				narrowed = true
				plan = narrowPlan(plan, i, one.Playbill.Dates, inHorizon)
			}
			continue
		}

		if one.Err != nil || one.ParseErr != nil {
			lastFail = one
			out.FailedDays = append(out.FailedDays, date)
			continue
		}

		// Чек сходимости дат. Тело этого дня уже приходило раньше — значит
		// источник запрошенную дату не различил, и спрашивать его дальше не о
		// чем. День без сеансов в сравнении не участвует: у многих каналов
		// пустой день — байт в байт одна и та же заглушка, и два таких дня
		// подряд обрубили бы обход, потеряв все дальнейшие дни с сеансами.
		if one.BodyHash != "" && len(one.Playbill.Showtimes) > 0 {
			days := showtimeDays(one.Playbill)
			if prev, ok := bodies[one.BodyHash]; ok {
				out.DateBlind = dateBlindReason(prev, date, days)
				break
			}
			bodies[one.BodyHash] = bodySeen{date: date, days: days}
		}

		got++
		out.Status = mergeStatus(out.Status, one.Status)
		out.BodySize += one.BodySize
		if out.Playbill.Cinema == "" {
			out.Playbill.Cinema = one.Playbill.Cinema
		}
		out.Playbill.Showtimes = appendNewShowtimes(out.Playbill.Showtimes, one.Playbill.Showtimes, seen)
		// Список дней источник повторяет на каждой странице — складывать его
		// сам с собой значит получить один и тот же горизонт по разу на день.
		out.Playbill.Dates = appendNewDates(out.Playbill.Dates, one.Playbill.Dates, seenDates)

		// Источник назвал свои дни — дальше идём только по ним. Список
		// пересекается с запрошенным горизонтом, а не заменяет его: ключ --days
		// остаётся верхней границей.
		if !narrowed && len(one.Playbill.Dates) > 0 {
			narrowed = true
			plan = narrowPlan(plan, i, one.Playbill.Dates, inHorizon)
		}
	}

	// Не ответил ни один день — наружу уходит настоящий отказ последнего
	// запроса, а не сводка о нём: классификатору нужны код и текст ошибки.
	if got == 0 {
		return lastFail
	}
	out.WindowFrom, out.WindowTo = sourceWindow(out.Playbill)
	return out
}

// bodySeen — тело ответа, уже полученное на какую-то дату.
//
// days хранится рядом с датой, потому что одного совпадения тел мало: по нему
// видно, что источник не различил даты, но не видно, кто датировал сеансы.
// Совпали и дни — значит датировал источник; разошлись — датировали мы.
type bodySeen struct {
	date string
	days []string
}

// dateBlindReason решает, кто датировал сеансы источника, отдавшего одно и то же
// тело на две разные даты.
//
// Даты сеансов сдвинулись вслед за запросом — датируем их МЫ, и приписывать
// этому дню нечего: наружу уходит причина, по которой окно площадки узкое. Не
// сдвинулись — источник датирует сеансы сам, его окно уже собрано первым
// ответом, и жаловаться не на что: лишними были только запросы.
func dateBlindReason(prev bodySeen, date string, days []string) string {
	if sameStrings(prev.days, days) {
		return ""
	}
	return fmt.Sprintf(
		"на %s пришло то же тело, что на %s: источник запрошенную дату не различает",
		date, prev.date)
}

// horizonPlan раскладывает запрошенное окно в список дней и множество их дат.
//
// Множество нужно, чтобы дни, названные самим источником, не вывели обход за
// границу запрошенного окна: у ГУМа собственный список тянется на полтора
// месяца вперёд, а спрашивали его про 28 дней.
func horizonPlan(from time.Time, days int) ([]time.Time, map[string]bool) {
	plan := make([]time.Time, 0, days)
	inHorizon := make(map[string]bool, days)
	for i := 0; i < days; i++ {
		d := from.AddDate(0, 0, i)
		plan = append(plan, d)
		inHorizon[d.Format("2006-01-02")] = true
	}
	return plan, inHorizon
}

// narrowPlan сужает остаток обхода до дней, которые назвал сам источник.
//
// Пройденное не трогается: done — индекс дня, ответ которого принёс список.
// Дни вне запрошенного горизонта отбрасываются.
func narrowPlan(plan []time.Time, done int, srcDates []string, inHorizon map[string]bool) []time.Time {
	seen := map[string]bool{}
	for _, d := range plan[:done+1] {
		seen[d.Format("2006-01-02")] = true
	}

	var rest []string
	for _, s := range srcDates {
		s = strings.TrimSpace(s)
		if len(s) < 10 {
			continue
		}
		s = s[:10]
		if !inHorizon[s] || seen[s] {
			continue
		}
		seen[s] = true
		rest = append(rest, s)
	}
	sort.Strings(rest)

	out := append([]time.Time{}, plan[:done+1]...)
	for _, s := range rest {
		t, err := time.ParseInLocation("2006-01-02", s, moscowTZ)
		if err != nil {
			continue
		}
		out = append(out, t)
	}
	return out
}

// showtimeDays — отсортированные уникальные даты сеансов афиши.
func showtimeDays(pb Playbill) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range pb.Showtimes {
		if len(s.StartsAt) < 10 {
			continue
		}
		day := s.StartsAt[:10]
		if seen[day] {
			continue
		}
		seen[day] = true
		out = append(out, day)
	}
	sort.Strings(out)
	return out
}

func sameStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// appendNewDates складывает дни источника без повторов: список приходит на
// каждой его странице целиком.
func appendNewDates(dst, src []string, seen map[string]bool) []string {
	for _, d := range src {
		if seen[d] {
			continue
		}
		seen[d] = true
		dst = append(dst, d)
	}
	sort.Strings(dst)
	return dst
}

// appendNewShowtimes добавляет к окну только те сеансы, которых в нём ещё нет.
//
// Канал, игнорирующий запрошенную дату, отдаёт на каждый день окна ОДИН И ТОТ ЖЕ
// ответ, и склейка без проверки складывает его сам с собой: на горизонте в N
// дней каждый сеанс повторяется N раз. Так и случилось с Pushka — 140 записей
// при 70 реальных временах.
//
// Список channelWindowWhole от этого защищает, но только пока в него не забыли
// внести очередной такой канал — а забыть легко, свойство видно лишь по коду
// запроса. Дедуп ловит любой канал с этим свойством, даже неизвестный.
//
// Признак совпадения — тот же отпечаток, которым сеансы различаются в отчёте:
// название, время, зал и формат. Площадка здесь одна на всё окно, поэтому в
// ключ не входит.
func appendNewShowtimes(dst, src []Showtime, seen map[string]bool) []Showtime {
	for _, s := range src {
		fp := showtimeFingerprint("", s)
		if seen[fp] {
			continue
		}
		seen[fp] = true
		dst = append(dst, s)
	}
	return dst
}

// fetchChannelDay — один запрос к каналу за одну дату.
func fetchChannelDay(c *Client, kind string, p ChannelParams, day time.Time) ChannelProbe {
	venue := p[pVenue]
	switch kind {
	case kindKaro:
		return fetchKaroDay(c, venue)
	case kindKinomax:
		return fetchOne(c,
			"https://api.kinomax.ru/rest/cinemas/"+url.PathEscape(venue)+
				"/sessions?date="+day.Format("2006-01-02"),
			parseKinomax)
	case kindCinemaStar:
		return fetchOne(c,
			"https://api.cinemastar.ru/theatre/"+url.PathEscape(venue),
			parseCinemaStar)
	case kindMoskino:
		return fetchOne(c,
			"https://mos-kino.ru/cinema/"+url.PathEscape(venue)+"/",
			func(body string) (Playbill, error) { return parseMoskino(body, day) })
	case kindCinemaPark:
		return fetchCinemaParkDay(venue, day)
	case kindCinema5:
		return fetchCinema5(c, venue)
	case kindPioner:
		return fetchOne(c, "https://pioner-cinema.ru/?date="+day.Format("2006-01-02"),
			func(body string) (Playbill, error) {
				return parsePioner(body, day.Format("2006-01-02"))
			})
	case kindPoklonka:
		return fetchOne(c, "https://poklonka-cinema.ru/films/",
			func(body string) (Playbill, error) { return parsePoklonka(body, day) })
	case kindMoskva:
		return fetchOne(c, "https://cinema.moscow/repertoire?date="+day.Format("2006-01-02"),
			func(body string) (Playbill, error) {
				return parseCinemaMoskva(body, day.Format("2006-01-02"))
			})
	case kindAlmaz:
		return fetchOne(c,
			"https://almazcinema.com/msk/cinema/"+url.PathEscape(venue)+"/schedule/",
			func(body string) (Playbill, error) {
				return parseAlmaz(body, day.Format("2006-01-02"))
			})
	case kindIllusion:
		return fetchOne(c, "https://illusion-cinema.ru/schedule/",
			func(body string) (Playbill, error) { return parseIllusion(body, day) })
	case kindLuxor:
		// Банка cookie обязательна: без неё сайт крутит редирект сам на себя,
		// пока в куке не сохранён выбор площадки (проверено на «Весне»).
		// Банка навешивается на ПЕРЕДАННЫЙ клиент, чтобы не потерять туннель.
		return fetchOne(c.withCookies(),
			"https://www.luxorfilm.ru/cinema/"+url.PathEscape(venue)+"/seances",
			func(body string) (Playbill, error) {
				return parseLuxor(body, day.Format("2006-01-02"))
			})
	case kindTretyakov:
		// venue здесь — название корпуса: строк реестра две, а страница одна.
		return fetchOne(c, "https://www.tretyakovgallery.ru/tickets/cinema/",
			func(body string) (Playbill, error) { return parseTretyakov(body, venue) })
	case kindJewish:
		return fetchOne(c, "https://www.jewish-museum.ru/events/",
			func(body string) (Playbill, error) { return parseJewishMuseum(body) })
	case kindRomanov:
		return fetchRomanovDay(c, day)
	case kindEtobilet:
		// Площадка живёт на своём домене, движок общий — как у p24.
		host := p[pHost]
		if host == "" {
			return ChannelProbe{Err: fmt.Errorf("каналу etobilet нужен домен площадки (host)")}
		}
		return fetchOne(c, "https://"+host+"/?date="+day.Format("02.01.2006"),
			func(body string) (Playbill, error) {
				return parseEtobilet(body, day.Format("2006-01-02"))
			})
	case kind5Zvezd:
		return fetchOne(c,
			"https://5zvezd.ru/schedule/"+url.PathEscape(venue)+
				"?date="+day.Format("02.01.2006"),
			func(body string) (Playbill, error) {
				return parseFiveStars(body, day.Format("2006-01-02"))
			})
	case kindKinoplan:
		return fetchKinoplanDay(c, venue, day)
	case kindP24:
		// Площадка живёт на СВОЁМ домене, движок общий. Без домена запрос
		// собрать не из чего, и молчать об этом нельзя.
		host := p[pHost]
		if host == "" {
			return ChannelProbe{Err: fmt.Errorf("каналу p24 нужен домен площадки (host)")}
		}
		return fetchOne(c,
			"https://"+host+"/?date="+day.Format("2006/01/02")+"&facility="+url.QueryEscape(venue),
			func(body string) (Playbill, error) {
				return parseP24(body, day.Format("2006-01-02"))
			})
	case kindPushka:
		return fetchPushkaDay(c, venue)
	case kindMori:
		// Дата — параметром. Без неё источник отдаёт сегодняшний день на любой
		// запрос (133453 байта против 162246 у `?date=2026-08-10`), а разбор
		// приписывал его сеансы запрошенной дате.
		return fetchOne(c,
			"https://mori.film/schedule/"+url.PathEscape(venue)+
				"?date="+day.Format("2006-01-02"),
			func(body string) (Playbill, error) {
				return parseMori(body, day.Format("2006-01-02"))
			})
	case kindHudozhestvenny:
		return fetchOne(c,
			"https://cinema1909.ru/schedule/"+day.Format("2006-01-02"),
			func(body string) (Playbill, error) {
				return parseHudozhestvenny(body, day.Format("2006-01-02"))
			})
	case kindPremierzal:
		// Сайт у каждой площадки свой, движок общий — без домена запрос собрать
		// не из чего, и молчать об этом нельзя.
		host := p[pHost]
		if host == "" {
			return ChannelProbe{Err: fmt.Errorf("каналу Премьерзала нужен домен площадки (host)")}
		}
		// Клиент с банкой cookie: без неё сайт крутит редирект сам на себя и
		// запрос умирает на десятом прыжке (проверено живьём).
		return fetchOne(newSessionClient(60, 3), "https://"+host+"/schedule",
			func(body string) (Playbill, error) {
				return parsePremierzal(body, day.Format("2006-01-02"))
			})
	case kindMirage:
		// У площадки свой адрес расписания, и дата стоит в самом пути. Общая
		// страница города отдаёт только ту площадку, что выбрана по умолчанию,
		// поэтому брать её нельзя: три площадки получили бы одно расписание
		// MARI. Домен сразу с www — на него ведёт редирект, и без него каждый
		// запрос идёт дважды.
		return fetchOne(c, mirageHost+"/msk/schedule/"+day.Format("02.01.2006")+
			"/cinema/"+url.PathEscape(venue)+"/",
			func(body string) (Playbill, error) {
				return parseMirage(body, venue, day.Format("2006-01-02"))
			})
	case kindGum:
		return fetchGumDay(c, day)
	}

	// Молчаливый пропуск здесь означал бы пустую афишу, а пустая афиша — «у
	// площадки нет сеансов». Поэтому вид без запроса это отказ, и видно, какой.
	return ChannelProbe{Err: fmt.Errorf("вид канала %q опрашивать нечем", kind)}
}

// gumSchedule — страница расписания кинозала ГУМа.
const gumSchedule = "https://gum.ru/kinozal/"

// fetchGumDay — расписание ГУМа за один день.
//
// Два шага, потому что день выбирается не адресом: сперва берётся страница со
// списком дней, потом нужный день запрашивается отправкой формы с его
// идентификатором. Пока этого не было, источник на все 28 дней окна отдавал
// сегодняшнюю страницу, а её сеансы разъезжались по датам, которых нет.
//
// Запрошенного дня нет в списке — источник про него сказал «не показываю»: это
// не отказ канала, и день остаётся непокрытым.
func fetchGumDay(c *Client, day time.Time) ChannelProbe {
	body, status, err := c.get(gumSchedule)
	out := ChannelProbe{Status: status, BodySize: len(body), Err: err}
	if err != nil {
		return out
	}

	date := day.Format("2006-01-02")
	var want gumDayOption
	days := gumDays(body, day)
	for _, d := range days {
		if d.date == date {
			want = d
			break
		}
	}
	if want.id == "" {
		// Дни источника наружу отдаём и здесь: по ним обход сузит остаток
		// горизонта и больше не станет спрашивать про дни, которых нет.
		for _, d := range days {
			out.Playbill.Dates = append(out.Playbill.Dates, d.date)
		}
		sort.Strings(out.Playbill.Dates)
		out.ParseErr = fmt.Errorf("%w: ГУМ не показывает %s", errDayNotPublished, date)
		return out
	}

	// Выбранный день страница уже показывает — второй запрос не нужен.
	if !want.selected {
		body, status, err = c.postForm(gumSchedule, url.Values{"SECTION_ID": {want.id}}, nil)
		out.Status = mergeStatus(out.Status, status)
		out.BodySize += len(body)
		if err != nil {
			out.Err = err
			return out
		}
	}

	sum := sha256.Sum256([]byte(body))
	out.BodyHash = hex.EncodeToString(sum[:8])
	out.Playbill, out.ParseErr = parseGum(body, day)
	return out
}

// fetchOne — общий скелет: один GET и один разбор.
func fetchOne(c *Client, addr string, parse func(string) (Playbill, error)) ChannelProbe {
	body, status, err := c.get(addr)
	out := ChannelProbe{Status: status, BodySize: len(body), Err: err}
	if err != nil {
		return out
	}
	sum := sha256.Sum256([]byte(body))
	out.BodyHash = hex.EncodeToString(sum[:8])
	pb, perr := parse(body)
	out.Playbill, out.ParseErr = pb, perr
	return out
}

// Заголовки кассы Kinoplan. Без x-platform касса отвечает 400 «Invalid headers».
const (
	kinoplanBase     = "https://kinokassa.kinoplan24.ru/api/v2"
	kinoplanPlatform = "widget"
)

// fetchKinoplanDay — два вызова: сперва токен площадки, потом её афиша.
//
// Токен НЕ хранится в реестре намеренно. Он выдаётся приложению виджета и
// протухает при его перевыпуске: сохранённый однажды, он однажды же начнёт
// отвечать 404 «App not found», и выглядеть это будет как исчезнувшая площадка.
// В реестре живёт номер виджета — он у площадки постоянный и виден на её сайте.
// kinoplanApp — то, что касса отдаёт про приложение виджета.
type kinoplanApp struct {
	Token   string `json:"token"`
	Cinemas []struct {
		ID     int `json:"id"`
		CityID int `json:"city_id"`
	} `json:"cinemas"`
}

// kinoplanCityOf — город ЗАПРОШЕННОЙ площадки. Ноль означает, что её в
// приложении нет вовсе.
//
// Отдельная функция, потому что здесь был тихий дефект: город брался у ПЕРВОЙ
// площадки списка. Приложение кассы бывает общим на несколько городов — у
// виджета ЗигЗага их три, Липецк (120), Люберцы (2465) и Москва (6552), и
// Липецк стоит первым. Запрос уходил за афишей Липецка, московских сеансов в
// ней не было, и канал отдавал пустую афишу при HTTP 200: выглядел живым, но
// молчащим, а площадка тихо выпадала из покрытия.
func kinoplanCityOf(app kinoplanApp, id int) int {
	for _, c := range app.Cinemas {
		if c.ID == id {
			return c.CityID
		}
	}
	return 0
}

func fetchKinoplanDay(c *Client, widget string, day time.Time) ChannelProbe {
	head := map[string]string{
		"x-platform":           kinoplanPlatform,
		"x-preferred-language": "ru",
	}

	appBody, status, err := c.getHeaders(
		kinoplanBase+"/app?with_cities=true&cinema_id="+url.QueryEscape(widget), head)
	out := ChannelProbe{Status: status, BodySize: len(appBody), Err: err}
	if err != nil {
		return out
	}

	var app kinoplanApp
	if err := json.Unmarshal([]byte(appBody), &app); err != nil {
		out.ParseErr = fmt.Errorf("разбор приложения Kinoplan: %w", err)
		return out
	}
	if app.Token == "" {
		out.ParseErr = fmt.Errorf("приложение Kinoplan %q не отдало токен", widget)
		return out
	}
	id, _ := strconv.Atoi(widget)
	city := kinoplanCityOf(app, id)
	if city == 0 {
		// Площадки нет в собственном приложении — это промах по идентификатору,
		// а не пустой день. Молча взять чужой город значило бы выдать чужую
		// афишу за свою.
		out.ParseErr = fmt.Errorf(
			"приложение Kinoplan не содержит площадку %s (в нём %d других)", widget, len(app.Cinemas))
		return out
	}

	head["x-application-token"] = app.Token
	body, status, err := c.getHeaders(fmt.Sprintf("%s/release/playbill?city_id=%d&date=%s",
		kinoplanBase, city, day.Format("2006-01-02")), head)
	out.Status, out.BodySize = status, out.BodySize+len(body)
	if err != nil {
		out.Err = err
		return out
	}

	// Отбор по площадке обязателен: приложение бывает общим на несколько
	// кинотеатров, и без него каждый получил бы расписание всех сразу.
	pb, perr := parseKinoplanFor(body, id)
	out.Playbill, out.ParseErr = pb, perr
	return out
}

// mergeStatus — какой код ответа канал показывает наружу за весь горизонт.
//
// Побеждает день, ПРИНЁСШИЙ ответ, а не последний по счёту. Разница видна на
// kinoteatr.ru: пустой день он отдаёт редиректом (см. fetchCinemaParkDay), и
// простое «последний выигрывает» позволило бы пустому дню в конце горизонта
// перебить код рабочих дней. Классификатор на всё, кроме 200, ставит suspect —
// то есть живой канал с полной афишей на неделю выглядел бы подозрительным
// из-за одного дня без сеансов.
func mergeStatus(have, next int) int {
	if have == 0 || (have/100 == 3 && next/100 != 3) {
		return next
	}
	return have
}

// fetchCinemaParkDay — афиша площадки kinoteatr.ru за одну дату.
//
// Своя функция ради одной вещи: у этого источника редирект — содержательный
// ответ. На дату, которой у площадки в расписании нет, он отвечает 301 на
// страницу-обёртку с ближайшим доступным днём. Клиент по умолчанию переход
// выполняет, разбор получает HTML вместо JSON и объявляет канал сломанным —
// именно так «СИНЕМА ПАРК МОСФИЛЬМ» выпал из покрытия, хотя канал жив и на
// завтрашнюю дату отдаёт афишу.
//
// Поэтому запрос идёт клиентом без переходов, а 30x читается как пустой день.
// Отличить пустой день от поломки иначе нечем: обёртка отдаёт нормальные 200 и
// разметку расписания — только чужого дня.
func fetchCinemaParkDay(venue string, day time.Time) ChannelProbe {
	date := day.Format("2006-01-02")
	addr := "https://kinoteatr.ru/raspisanie-kinoteatrov/" + url.PathEscape(venue) +
		"/?date=" + date + "&ajax=1"

	c := newNoRedirectClient(60, 3)
	body, status, err := c.get(addr)
	out := ChannelProbe{Status: status, BodySize: len(body), Err: err}
	if err != nil {
		return out
	}
	if status >= 300 && status < 400 {
		// Пустой день, а не отказ: канал ответил, сеансов на эту дату нет.
		// Дни источника здесь не проставляются: запрошенная дата — не список
		// того, что источник публикует, а эхо вопроса, и обход по ней сузился
		// бы до одного дня.
		out.Playbill = Playbill{}
		return out
	}

	out.Playbill, out.ParseErr = parseCinemaPark(body, date)
	return out
}

// fetchCinema5 — весь горизонт площадки Синема 5, то есть два дня.
//
// Два запроса вместо одного: у источника афиша разложена по страницам `today` и
// `tomorrow`, произвольная дата в пути отдаёт пустой список. Сегодня и завтра
// собираются вместе, потому что для канала это и есть всё, что у него есть.
//
// Отказ ВТОРОГО дня афишу первого не отменяет: сеансы сегодняшнего дня — факт,
// добытый живым ответом, и выбрасывать их из-за завтрашнего дня значило бы
// потерять данные там, где источник ответил.
func fetchCinema5(c *Client, venue string) ChannelProbe {
	id, err := strconv.Atoi(strings.TrimSpace(venue))
	if err != nil {
		return ChannelProbe{Err: fmt.Errorf("каналу Синема 5 нужен числовой id площадки, получено %q", venue)}
	}

	var out ChannelProbe
	var lastFail ChannelProbe
	got := 0

	for _, page := range []string{"today", "tomorrow"} {
		one := fetchOne(c,
			"https://cinema5.ru/api/v1/movies/page/"+page+"?cinemaIds="+strconv.Itoa(id),
			func(body string) (Playbill, error) { return parseCinema5(body, id) })

		if one.Err != nil || one.ParseErr != nil {
			lastFail = one
			out.FailedDays = append(out.FailedDays, page)
			continue
		}
		got++
		out.Status = mergeStatus(out.Status, one.Status)
		out.BodySize += one.BodySize
		if out.Playbill.Cinema == "" {
			out.Playbill.Cinema = one.Playbill.Cinema
		}
		out.Playbill.Showtimes = append(out.Playbill.Showtimes, one.Playbill.Showtimes...)
		out.Playbill.Dates = append(out.Playbill.Dates, one.Playbill.Dates...)
	}

	if got == 0 {
		return lastFail
	}
	return out
}

// fetchRomanovDay — расписание Романова за одну дату.
//
// Ручка принимает дату в теле POST, а не в адресе, и требует ключ заголовком.
func fetchRomanovDay(c *Client, day time.Time) ChannelProbe {
	body, status, err := c.postJSON(romanovAPI+"/seanslist",
		fmt.Sprintf(`{"TIMETABLE_DATE":%q}`, day.Format("02.01.2006")),
		map[string]string{"x-api-key": romanovAPIKey, "accept": "application/json"})

	out := ChannelProbe{Status: status, BodySize: len(body), Err: err}
	if err != nil {
		return out
	}
	out.Playbill, out.ParseErr = parseRomanov(body, day.Format("2006-01-02"))
	return out
}

// fetchPushkaDay — два вызова ОДНИМ сессионным клиентом.
//
// Площадку задаёт кука cinema_id, а ставит её страница площадки. Ни путь, ни
// Referer, ни query на выдачу не влияют — проверено живьём всеми тремя
// способами, и голый запрос молча возвращает дефолтный «Клён». Молча — то есть
// расписание чужой площадки выглядело бы как своё.
func fetchPushkaDay(c *Client, slug string) ChannelProbe {
	session := newSessionClient(60, 3)

	page, status, err := session.get("https://cinema.pushka.club/moscow/" + url.PathEscape(slug))
	out := ChannelProbe{Status: status, BodySize: len(page), Err: err}
	if err != nil {
		return out
	}

	body, status, err := session.get("https://cinema.pushka.club/!/ajax/schedule")
	out.Status, out.BodySize = status, out.BodySize+len(body)
	if err != nil {
		out.Err = err
		return out
	}

	pb, perr := parsePushka(body)
	out.Playbill, out.ParseErr = pb, perr
	return out
}

// fetchKaroDay — единственный канал с двумя вызовами.
//
// Сеансы приходят плоским списком с film_id, а названия живут в обычном ответе
// того же адреса. Без второго вызова у сеансов нет названия вовсе, то есть
// искать в них фильм нечем.
//
// Даты запрос не принимает: с параметром date плоский ответ пуст, без него
// приходит весь горизонт сети.
func fetchKaroDay(c *Client, venue string) ChannelProbe {
	base := "https://api.karofilm.ru/cinema-schedule?cinema_id=" + url.QueryEscape(venue)

	flat, status, err := c.get(base + "&schema=flat")
	out := ChannelProbe{Status: status, BodySize: len(flat), Err: err}
	if err != nil {
		return out
	}

	films, status, err := c.get(base)
	out.Status, out.BodySize = status, out.BodySize+len(films)
	if err != nil {
		out.Err = err
		return out
	}

	pb, perr := parseKaroSchedule(flat, films)
	out.Playbill, out.ParseErr = pb, perr
	return out
}

// ——— Яндекс Афиша: второй источник ———
//
// Доступ держится на двух неочевидных условиях, и оба выяснены живой пробой.
//
// Первое: заголовок x-force-cors-preflight. Без него nginx Афиши отвечает 405
// Not Allowed — причём на ЛЮБУЮ операцию, включая те, что шлёт сам сайт. То
// есть 405 здесь значит «нас не пустили», а не «такой операции нет».
//
// Второе: имя операции сервер берёт из поля operationName, а не из текста
// запроса. Запрос с именем внутри query, но без этого поля, отвергается с
// «Unknown operation named 'unknown'».
const (
	yandexGQL = "https://afisha.yandex.ru/api/graphql?city=moscow&version=581.0.0"

	// yandexSlugBase — префикс адреса фильма, из которого берётся его id.
	yandexSlugBase = "/moscow/cinema/"
)

// yandexHeaders — обязательный набор. Пустой x-csrf-token шлёт и сам сайт.
func yandexHeaders() map[string]string {
	return map[string]string{
		"content-type":           "application/json",
		"x-force-cors-preflight": "1",
		"x-csrf-token":           "",
		"accept-language":        "ru-RU",
		"accept":                 "*/*",
	}
}

// yandexEventID переводит слаг фильма в идентификатор Афиши.
func yandexEventID(c *Client, slug string) (string, int, error) {
	body := fmt.Sprintf(`{"operationName":"UrlInfoQuery","variables":{"url":%q},`+
		`"query":"query UrlInfoQuery($url: String!) { urlInfo(url: $url) { status type params { ... on UrlInfoEvent { eventId: id } } } }"}`,
		yandexSlugBase+slug)

	resp, status, err := c.postJSON(yandexGQL, body, yandexHeaders())
	if err != nil {
		return "", status, err
	}

	var out struct {
		Data struct {
			URLInfo struct {
				Status string `json:"status"`
				Params struct {
					EventID string `json:"eventId"`
				} `json:"params"`
			} `json:"urlInfo"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(resp), &out); err != nil {
		return "", status, fmt.Errorf("Яндекс Афиша: ответ об адресе не читается: %w", err)
	}
	if id := strings.TrimSpace(out.Data.URLInfo.Params.EventID); id != "" {
		return id, status, nil
	}
	return "", status, fmt.Errorf("Яндекс Афиша: у слага %q нет идентификатора фильма (status %q)",
		slug, out.Data.URLInfo.Status)
}

// Наборы полей события. Их ДВА, и это не дублирование: поиск отдаёт EventPreview,
// у которого нет ни года, ни хронометража — именно тех полей, которыми сверяется
// профиль. Полная карточка есть только у event(id).
const (
	yandexEventCardFields    = "id title url originalTitle year dateReleased duration"
	yandexEventPreviewFields = "id title url originalTitle dateReleased"
)

// fetchYandexEventCard берёт карточку фильма по его идентификатору.
//
// Единственный способ узнать, ПРО ТОТ ЛИ фильм спросили. Расписание на этот
// вопрос не отвечает: у чужого фильма-однофамильца оно просто пустое, и от
// честного «нигде не идёт» неотличимо.
func fetchYandexEventCard(c *Client, id string) (YandexEvent, int, error) {
	body := fmt.Sprintf(`{"operationName":"EventQuery","variables":{"id":%q},`+
		`"query":"query EventQuery($id: ID!) { event(id: $id) { %s } }"}`, id, yandexEventCardFields)

	resp, status, err := c.postJSON(yandexGQL, body, yandexHeaders())
	if err != nil {
		return YandexEvent{}, status, err
	}
	ev, perr := parseYandexEventCard(resp)
	if perr != nil {
		return YandexEvent{}, status, perr
	}
	return ev, status, nil
}

// findYandexEvents ищет идущие в городе фильмы по названию.
//
// Тег cinema обязателен: без него в выдачу лезут концерты и спектакли с тем же
// словом в названии. Ищутся ТОЛЬКО идущие сейчас — этим и снимается ловушка
// одноимённого фильма прошлых лет, которую даёт поиск по адресу страницы.
func findYandexEvents(c *Client, title string) ([]YandexEvent, int, error) {
	body := fmt.Sprintf(`{"operationName":"ActualEventsQuery","variables":{"q":%q},`+
		`"query":"query ActualEventsQuery($q: String) { actualEvents(search: $q, tags: [\"cinema\"], `+
		`paging: {limit: 20, offset: 0}) { items { event { %s } } } }"}`,
		strings.TrimSpace(title), yandexEventPreviewFields)

	resp, status, err := c.postJSON(yandexGQL, body, yandexHeaders())
	if err != nil {
		return nil, status, err
	}
	events, perr := parseYandexSearch(resp)
	if perr != nil {
		return nil, status, perr
	}
	return events, status, nil
}

// fetchYandexScheduleByID берёт сеансы фильма по всем площадкам города.
//
// Горизонт задаётся периодом в днях и приходит ОДНИМ ответом — обходить
// площадки не нужно, и это главное отличие от собственных каналов.
//
// На входе идентификатор события, а не адрес страницы: адрес ведёт на фильм
// любого года, и опознание фильма — отдельная работа, которая к моменту
// запроса расписания уже сделана.
func fetchYandexScheduleByID(c *Client, id string, from time.Time, days int) ([]AggregatorSession, int, error) {
	if days < 1 {
		days = 1
	}

	body := fmt.Sprintf(`{"operationName":"EventScheduleOtherQuery","variables":{"id":%q,`+
		`"dates":{"date":%q,"period":%d}},`+
		`"query":"query EventScheduleOtherQuery($id: ID!, $dates: DaysIntervalInput!) `+
		`{ eventScheduleOther(id: $id, dates: $dates) { items: byDate { date sessions `+
		`{ place { id url title address coordinates { latitude longitude } } `+
		`session { datetime hall: hallName ticket { saleStatus price { min max currency } } } } } } }"}`,
		id, from.Format("2006-01-02"), days)

	resp, status, err := c.postJSON(yandexGQL, body, yandexHeaders())
	if err != nil {
		return nil, status, err
	}

	sessions, perr := parseYandexSchedule(resp)
	if perr != nil {
		return nil, status, perr
	}
	return sessions, status, nil
}

// ——— kinoafisha: третий слой ———
//
// Устроен иначе всех: расписание отдаётся серверным HTML, но постранично ПО
// ДАТАМ. Первичная страница фильма наливает целиком только первую дату своего
// горизонта, остальные приходят отдельным запросом.
//
// Условие догрузки выяснено перехватом в браузере и стоило нескольких попыток:
//
//   - метод POST, а не GET. GET на тот же адрес отдаёт 301, и это выглядит как
//     «такой даты нет», хотя дата есть;
//   - обязателен заголовок x-request-ajax: 1. Ни X-Requested-With, ни Accept:
//     application/json, ни Referer, ни куки сессии не заменяют его — без него
//     тот же POST отвечает пустым телом.
//
// Ответ — JSON {status, html, css}, само расписание лежит в html и разбирается
// тем же разбором, что и первичная страница.
const (
	kinoafishaBase       = "https://www.kinoafisha.info"
	kinoafishaMovieBase  = kinoafishaBase + "/russia/msk/movies/"
	kinoafishaCinemaBase = kinoafishaBase + "/russia/msk/cinema/"
)

// kinoafishaHeaders — набор для догрузки даты.
func kinoafishaHeaders() map[string]string {
	return map[string]string{"x-request-ajax": "1"}
}

// kinoafishaNextRe — сколько строк даты уже отрисовано в первичной странице.
//
// Значение skip приходит вместе с датой в атрибуте data-schedule-next; своим
// числом его подменять нельзя — сервер отдаёт ровно остаток после skip.
var kinoafishaNextRe = regexp.MustCompile(`data-schedule-next=.\{"next":"\?date=(\d{4}-\d{2}-\d{2})&skip=(\d+)"\}`)

// Разбор выдачи поиска. Блок карточки несёт название, год и ссылку в РАЗНЫХ
// узлах, поэтому берётся по кускам, а не одной регуляркой на всё.
const kinoafishaCardOpen = `<div class="shortList_item">`

var (
	kinoafishaCardName = regexp.MustCompile(`<span class="shortList_name">([^<]+)</span>`)
	kinoafishaCardInfo = regexp.MustCompile(`<span class="shortList_info">\s*(\d{4})`)
	kinoafishaCardRef  = regexp.MustCompile(`class="shortList_ref" href="[^"]*?/movies/(\d+)/"`)
)

// kinoafishaGet — GET к источнику с повтором на 403.
//
// За источником стоит ddos-guard, и доступ он закрывает полосами: 05.08.2026
// днём обычный Go-клиент получал 403 на любой запрос около получаса, а через час
// тот же код без единой правки отвечал 200 двадцать раз из двадцати. Ни один
// подбор запроса полосу не пробил — ни снятие Accept-Language, ни HTTP/1.1, ни
// банка cookie, ни настройки TLS, — а сама она прошла.
//
// Отсюда трактовка: 403 у ЭТОГО источника означает «сейчас закрыто», а не «нам
// сюда нельзя». Повтор берёт короткую рябь; длинную полосу он не лечит, и тогда
// это честный отказ слоя, а не пустая афиша.
func kinoafishaGet(c *Client, addr string) (string, error) {
	var last error
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			time.Sleep(time.Duration(attempt) * 3 * time.Second)
		}
		body, status, err := c.get(addr)
		switch {
		case err != nil && status != 403:
			return "", err
		case status == 403:
			last = fmt.Errorf(
				"kinoafisha: %s ответил 403 после %d попыток — источник закрыл доступ полосой",
				addr, attempt+1)
			continue
		case status != 200:
			return "", fmt.Errorf("kinoafisha: %s ответил %d", addr, status)
		default:
			return body, nil
		}
	}
	return "", last
}

// fetchKinoafishaMovie берёт первичную страницу фильма.
func fetchKinoafishaMovie(c *Client, id string) (string, error) {
	return kinoafishaGet(c, kinoafishaMovieBase+url.PathEscape(id)+"/")
}

// fetchKinoafishaVenueGeo берёт координаты площадки с её собственной страницы.
//
// В расписании фильма координат нет вовсе — только название и адрес. Без точки
// привязка сваливается на именную ступень, а она разрешена лишь для строк
// реестра БЕЗ координат: обе московские площадки «Паука» уходили в «не
// опознано» ровно по этой причине, хотя одна из них есть в реестре.
func fetchKinoafishaVenueGeo(c *Client, id string) (float64, float64, error) {
	body, err := kinoafishaGet(c, kinoafishaCinemaBase+url.PathEscape(id)+"/")
	if err != nil {
		return 0, 0, err
	}
	return parseKinoafishaVenueGeo(body, id)
}

// fetchKinoafishaDate догружает остаток одной даты.
func fetchKinoafishaDate(c *Client, id, date string, skip int) (string, error) {
	addr := fmt.Sprintf("%s%s/?date=%s&skip=%d", kinoafishaMovieBase, url.PathEscape(id), date, skip)

	body, status, err := c.do("POST", addr, "", kinoafishaHeaders())
	if err != nil {
		return "", err
	}
	// 301 здесь означает ровно одно: заголовок не доехал. Прочитать это как
	// «на дату сеансов нет» нельзя — слой замолчал бы там, где у него всё
	// расписание.
	if status != 200 {
		return "", fmt.Errorf("kinoafisha: догрузка %s ответила %d — проверь заголовок x-request-ajax", date, status)
	}

	var out struct {
		Status bool   `json:"status"`
		HTML   string `json:"html"`
	}
	if err := json.Unmarshal([]byte(body), &out); err != nil {
		return "", fmt.Errorf("kinoafisha: ответ догрузки %s не читается как JSON: %w", date, err)
	}
	if !out.Status {
		return "", fmt.Errorf("kinoafisha: догрузка %s отвергнута источником", date)
	}
	return out.HTML, nil
}

// kinoafishaPending — какие даты и с каким skip нужно догрузить.
func kinoafishaPending(page string) map[string]int {
	out := map[string]int{}
	for _, m := range kinoafishaNextRe.FindAllStringSubmatch(page, -1) {
		n, err := strconv.Atoi(m[2])
		if err != nil {
			continue
		}
		out[m[1]] = n
	}
	return out
}

// findKinoafishaMovies ищет фильм по названию.
//
// Поиск КАТАЛОЖНЫЙ, а не по идущим в городе, и этим принципиально отличается от
// яндексовского: запрос «Майкл» отдал 20 карточек, из них с точным названием
// три — 2026, 2023 и 1996 года. Поэтому кандидаты сужаются точным совпадением
// названия ещё до того, как в дело вступит правило про год.
func findKinoafishaMovies(c *Client, title string) ([]KinoafishaMovie, error) {
	addr := kinoafishaBase + "/search/?q=" + url.QueryEscape(strings.TrimSpace(title)) + "&type=movies"
	body, err := kinoafishaGet(c, addr)
	if err != nil {
		return nil, err
	}

	want := normalizeFilmTitle(title)
	var out []KinoafishaMovie
	seen := map[string]bool{}
	for _, card := range strings.Split(body, kinoafishaCardOpen)[1:] {
		name := kinoafishaCardName.FindStringSubmatch(card)
		ref := kinoafishaCardRef.FindStringSubmatch(card)
		if name == nil || ref == nil || seen[ref[1]] {
			continue
		}
		title := strings.TrimSpace(html.UnescapeString(name[1]))
		if normalizeFilmTitle(title) != want {
			continue
		}
		seen[ref[1]] = true

		m := KinoafishaMovie{ID: ref[1], Title: title}
		// Год лежит в той же карточке («2026, США») — отдельный запрос за ним
		// не нужен, а без него одноимённые фильмы неразличимы.
		if y := kinoafishaCardInfo.FindStringSubmatch(card); y != nil {
			m.Year, _ = strconv.Atoi(y[1])
		}
		out = append(out, m)
	}
	return out, nil
}
