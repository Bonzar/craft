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
	"fmt"
	"net/url"
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
func fetchChannel(c *Client, kind, venue string, from time.Time, days int) ChannelProbe {
	if days < 1 {
		days = 1
	}
	if channelWindowWhole[kind] {
		return fetchChannelDay(c, kind, venue, from)
	}

	var out ChannelProbe
	var lastFail ChannelProbe
	got := 0

	for i := 0; i < days; i++ {
		day := from.AddDate(0, 0, i)
		one := fetchChannelDay(c, kind, venue, day)

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
func fetchChannelDay(c *Client, kind, venue string, day time.Time) ChannelProbe {
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
