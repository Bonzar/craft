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
	"encoding/json"
	"fmt"
	"net/url"
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
		return fetchChannelDay(c, kind, p, from)
	}

	var out ChannelProbe
	var lastFail ChannelProbe
	got := 0

	for i := 0; i < days; i++ {
		day := from.AddDate(0, 0, i)
		one := fetchChannelDay(c, kind, p, day)

		if one.Err != nil || one.ParseErr != nil {
			lastFail = one
			out.FailedDays = append(out.FailedDays, day.Format("2006-01-02"))
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

	// Не ответил ни один день — наружу уходит настоящий отказ последнего
	// запроса, а не сводка о нём: классификатору нужны код и текст ошибки.
	if got == 0 {
		return lastFail
	}
	return out
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
		return fetchOne(c,
			"https://mori.film/schedule/"+url.PathEscape(venue),
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
		// У площадки свой адрес расписания. Общая страница города отдаёт только
		// ту, что выбрана по умолчанию, поэтому брать её нельзя: три площадки
		// получили бы одно и то же расписание MARI.
		return fetchOne(c, "https://mirage.ru/msk/schedule/cinema/"+url.PathEscape(venue)+"/",
			func(body string) (Playbill, error) {
				return parseMirage(body, venue, day.Format("2006-01-02"))
			})
	case kindGum:
		return fetchOne(c, "https://gum.ru/kinozal/",
			func(body string) (Playbill, error) {
				return parseGum(body, day.Format("2006-01-02"))
			})
	}

	// Молчаливый пропуск здесь означал бы пустую афишу, а пустая афиша — «у
	// площадки нет сеансов». Поэтому вид без запроса это отказ, и видно, какой.
	return ChannelProbe{Err: fmt.Errorf("вид канала %q опрашивать нечем", kind)}
}

// fetchOne — общий скелет: один GET и один разбор.
func fetchOne(c *Client, addr string, parse func(string) (Playbill, error)) ChannelProbe {
	body, status, err := c.get(addr)
	out := ChannelProbe{Status: status, BodySize: len(body), Err: err}
	if err != nil {
		return out
	}
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
		out.Playbill = Playbill{Dates: []string{date}}
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
