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
		out.Status = one.Status
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
		return fetchOne(c,
			"https://kinoteatr.ru/raspisanie-kinoteatrov/"+url.PathEscape(venue)+
				"/?date="+day.Format("2006-01-02")+"&ajax=1",
			func(body string) (Playbill, error) {
				return parseCinemaPark(body, day.Format("2006-01-02"))
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

	var app struct {
		Token   string `json:"token"`
		Cinemas []struct {
			ID     int `json:"id"`
			CityID int `json:"city_id"`
		} `json:"cinemas"`
	}
	if err := json.Unmarshal([]byte(appBody), &app); err != nil {
		out.ParseErr = fmt.Errorf("разбор приложения Kinoplan: %w", err)
		return out
	}
	if app.Token == "" {
		out.ParseErr = fmt.Errorf("приложение Kinoplan %q не отдало токен", widget)
		return out
	}
	city := 0
	if len(app.Cinemas) > 0 {
		city = app.Cinemas[0].CityID
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
	id, _ := strconv.Atoi(widget)
	pb, perr := parseKinoplanFor(body, id)
	out.Playbill, out.ParseErr = pb, perr
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
