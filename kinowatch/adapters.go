package main

// Адаптеры собственных каналов сетей — ядро первого слоя.
//
// На каждую площадку идёт СВОЙ запрос к её собственной кассе, а не выгрузка по
// городу: в этом вся разница с агрегатором. Разведка 31.07 подтвердила такой
// канал у всех сетей московского листинга, кроме двух (Мираж — SPA, Люксор —
// JS-челлендж за российским IP).
//
// Адаптер тянет афишу площадки ЦЕЛИКОМ, а не ищет один фильм. Причина не в
// удобстве: по полному списку считается живость источника (пустая афиша при
// HTTP 200 — это «источник сломался», а не «фильма нет»), и по нему же ловится
// маскировка, которую точечный поиск по названию пропустил бы по определению.

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Типы источников. Значение хранится в реестре (SourceKind), поэтому строки —
// часть контракта с Craft.
//
// Их меньше, чем сетей: два движка оказались общими для нескольких брендов.
// Kinoplan держит кассы Космика, Киноквартала, Silver Cinema и Вики Синема,
// p24.app — Нивады и Колибри. Адаптер пишется на движок, а не на вывеску.
const (
	kindKinomax    = "kinomax"    // JSON api.kinomax.ru — он же Созвездие и Кинообслуживание
	kindKaro       = "karo"       // JSON api.karofilm.ru, два вызова
	kindCinemaStar = "cinemastar" // JSON api.cinemastar.ru, всё окно сразу
	kindCinemaPark = "cinemapark" // kinoteatr.ru, JSON-обёртка над HTML, только через туннель
	kindKinoplan   = "kinoplan"   // kinokassa.kinoplan24.ru, токен на площадку
	kindMoskino    = "moskino"    // HTML mos-kino.ru
	kindMori       = "mori"       // HTML mori.film
	kind5Zvezd     = "5zvezd"     // HTML 5zvezd.ru
)

// Showtime — один сеанс в том виде, в каком его отдал источник.
//
// Поля намеренно необязательные: разведка показала, что источники отдают разное.
// Номер зала есть у «Пяти звёзд» и Поклонки, у Mori в этом месте формат
// (2Д, ВИП 2Д), у СИНЕМА ПАРК — класс зала (Стандарт, Комфорт). Цены нет у
// «Пяти звёзд». Хронометраж есть у Киномакса и отсутствует у КАРО.
//
// Пустое поле остаётся пустым: выдуманное значение хуже отсутствующего, потому
// что участвует в ключе сеанса и в матчинге.
type Showtime struct {
	Film     string `json:"film"`
	StartsAt string `json:"startsAt"` // RFC3339, московская зона

	// Hall — НОМЕР зала и только он. Технология показа и класс зала идут в
	// Format: «2Д» в Hall означало бы, что два сеанса одного фильма в разных
	// залах одного формата схлопнутся в один ключ.
	Hall   string `json:"hall,omitempty"`
	Format string `json:"format,omitempty"`

	PriceMin  int    `json:"priceMin,omitempty"`
	PriceMax  int    `json:"priceMax,omitempty"`
	SourceID  string `json:"sourceId,omitempty"`
	OnSale    bool   `json:"onSale"`
	DeepLink  string `json:"deepLink,omitempty"`
	DurationM int    `json:"durationMin,omitempty"`

	// FilmFiscal — название для кассового чека, если источник его отдаёт.
	//
	// У Киномакса это признак серого проката: из 19 фильмов площадки поле
	// заполнено ровно у тех, что идут «предсеансовым обслуживанием» («Одиссея»
	// → «Прощание», «Миньоны и монстры» → «Сказка на ночь»). Схема обратная
	// ожидаемой — в афише настоящее название, короткометражка прячется в чек.
	//
	// Обратной проверки нет: маскированный сеанс с пустым полем не встречался,
	// но и не искался. Поэтому непустое значение — сильный признак, а пустое
	// ничего не опровергает.
	FilmFiscal string `json:"filmFiscal,omitempty"`
}

// Playbill — афиша площадки за один запрос.
type Playbill struct {
	Cinema    string     `json:"cinema"`
	Showtimes []Showtime `json:"showtimes"`

	// Dates — даты, которые источник считает доступными. Берём оттуда, где
	// источник их отдаёт (Киномакс, КАРО), вместо того чтобы гадать глубину
	// горизонта: она у всех разная.
	Dates []string `json:"dates,omitempty"`
}

// moscowTZ — источники говорят о московском времени, но зону не указывают.
var moscowTZ = time.FixedZone("MSK", 3*60*60)

// joinDateTime собирает момент сеанса из даты и времени источника.
func joinDateTime(date, hhmm string) (string, error) {
	t, err := time.ParseInLocation("2006-01-02 15:04",
		strings.TrimSpace(date)+" "+strings.TrimSpace(hhmm), moscowTZ)
	if err != nil {
		return "", fmt.Errorf("время сеанса %q %q: %w", date, hhmm, err)
	}
	return t.Format(time.RFC3339), nil
}

// ——— Киномакс ———

type kinomaxResponse struct {
	SelectedDate string `json:"selectedDate"`
	Cinema       struct {
		Ident string `json:"ident"`
		Name  string `json:"name"`
	} `json:"cinema"`
	Dates  []string `json:"dates"`
	Movies []struct {
		Name       string `json:"name"`
		NameFiscal string `json:"nameFiscal"`
		Timeline   struct {
			Hours   int `json:"hours"`
			Minutes int `json:"minutes"`
		} `json:"timeline"`
		Sessions []struct {
			ID   int64  `json:"id"`
			Time string `json:"time"`
			// Цена приходит строкой двух видов: «990» — единая цена на сеанс,
			// «от 500» — нижняя граница. Разница смысловая, и терять её нельзя.
			Price    string `json:"price"`
			IsPassed bool   `json:"isPassed"`
			FormatID int    `json:"formatId"`
		} `json:"sessions"`
	} `json:"movies"`
	SessionFormats []struct {
		ID   int    `json:"id"`
		Name string `json:"name"`
	} `json:"sessionFormats"`
}

// parseKinomax разбирает ответ api.kinomax.ru.
//
// Номера зала в ответе нет вовсе, есть только формат — Hall остаётся пустым.
// Цена приходит строкой-подписью, разбор в parsePriceLabel.
func parseKinomax(body string) (Playbill, error) {
	var resp kinomaxResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return Playbill{}, fmt.Errorf("разбор ответа Киномакса: %w", err)
	}

	formats := map[int]string{}
	for _, f := range resp.SessionFormats {
		formats[f.ID] = f.Name
	}

	pb := Playbill{Cinema: strings.TrimSpace(resp.Cinema.Name), Dates: resp.Dates}

	for _, m := range resp.Movies {
		dur := m.Timeline.Hours*60 + m.Timeline.Minutes
		for _, s := range m.Sessions {
			at, err := joinDateTime(resp.SelectedDate, s.Time)
			if err != nil {
				continue
			}
			min, max := parsePriceLabel(s.Price)
			pb.Showtimes = append(pb.Showtimes, Showtime{
				Film:       strings.TrimSpace(m.Name),
				FilmFiscal: strings.TrimSpace(m.NameFiscal),
				StartsAt:   at,
				Format:     formats[s.FormatID],
				PriceMin:   min,
				PriceMax:   max,
				SourceID:   fmt.Sprintf("%d", s.ID),
				// isPassed — сеанс уже начался, билетов на него больше нет.
				OnSale:    !s.IsPassed,
				DurationM: dur,
			})
		}
	}
	return pb, nil
}

// parsePriceLabel разбирает цену-подпись вида «990» или «от 500».
//
// Возвращает нижнюю и верхнюю границы. У «от 500» верхней границы нет, и
// выдумывать её нельзя: источник сообщил ровно то, что сообщил. У «990» цена
// единая, поэтому обе границы совпадают — иначе в отчёте нельзя было бы
// отличить «билет стоит 990» от «билеты от 990».
func parsePriceLabel(s string) (min, max int) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, 0
	}
	openEnded := strings.HasPrefix(s, "от")

	digits := strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' {
			return r
		}
		return -1
	}, s)
	if digits == "" {
		return 0, 0
	}
	v, err := strconv.Atoi(digits)
	if err != nil {
		return 0, 0
	}
	if openEnded {
		return v, 0
	}
	return v, v
}

// ——— КАРО ———

type karoFlatResponse struct {
	Data struct {
		Items []struct {
			ID            int64  `json:"id"`
			FilmID        int    `json:"film_id"`
			FormatID      int    `json:"format_id"`
			Date          string `json:"date"`
			Time          string `json:"time"`
			StandardPrice int    `json:"standard_price"`
		} `json:"items"`
	} `json:"data"`
}

type karoFilmsResponse struct {
	Data struct {
		Info struct {
			Name string `json:"name"`
		} `json:"info"`
		Items []struct {
			ID   int    `json:"id"`
			Name string `json:"name"`
		} `json:"items"`
		// formats у КАРО — просто список id, доступных площадке, без названий.
		// Расшифровать format_id сеанса нечем, поэтому Format остаётся пустым.
		Formats  []int    `json:"formats"`
		DateList []string `json:"datelist"`
	} `json:"data"`
}

// parseKaro склеивает два ответа КАРО: сеансы приходят с film_id, а названия
// живут в отдельном вызове. Без второго вызова у сеансов нет названия вовсе,
// поэтому адаптер обязан делать оба.
//
// Хронометража и описания КАРО не отдаёт ни в одном из них. Значит уровни
// каскада «известная обёртка + аномальный хронометраж» и «синопсис» на этих 16
// строках реестра неприменимы, и находка по одному названию обязана получать
// пониженную уверенность — иначе непроверяемое выглядело бы как проверенное.
func parseKaroSchedule(flatBody, filmsBody string) (Playbill, error) {
	var flat karoFlatResponse
	if err := json.Unmarshal([]byte(flatBody), &flat); err != nil {
		return Playbill{}, fmt.Errorf("разбор сеансов КАРО: %w", err)
	}
	var films karoFilmsResponse
	if err := json.Unmarshal([]byte(filmsBody), &films); err != nil {
		return Playbill{}, fmt.Errorf("разбор фильмов КАРО: %w", err)
	}

	nameByID := map[int]string{}
	for _, f := range films.Data.Items {
		nameByID[f.ID] = strings.TrimSpace(f.Name)
	}
	pb := Playbill{Cinema: strings.TrimSpace(films.Data.Info.Name), Dates: films.Data.DateList}
	for _, s := range flat.Data.Items {
		at, err := joinDateTime(s.Date, s.Time)
		if err != nil {
			continue
		}
		pb.Showtimes = append(pb.Showtimes, Showtime{
			// Название может не найтись: справочник отдаёт репертуар на сегодня,
			// а сеансы — на запрошенную дату. Пустое имя лучше подставленного:
			// каскад матчинга разберётся с пустым, а чужой фильм молча
			// превратился бы в ложную находку.
			Film:     nameByID[s.FilmID],
			StartsAt: at,
			// Цена у КАРО в копейках: 22000 — это 220 рублей, а не 22 тысячи.
			PriceMin: s.StandardPrice / 100,
			SourceID: fmt.Sprintf("%d", s.ID),
			OnSale:   true,
		})
	}
	return pb, nil
}

// ——— СИНЕМА-СТАР ———

type cinemaStarResponse struct {
	Data struct {
		Theatre struct {
			Name string `json:"name"`
		} `json:"theatre"`
		Schedule struct {
			Dates []string `json:"dates"`
			Items []struct {
				Film struct {
					Name string `json:"name"`
				} `json:"film"`
				Formats []struct {
					Format   string `json:"format"`
					Sessions []struct {
						ID            int64  `json:"id"`
						BusinessDate  string `json:"business_date"`
						Showtime      string `json:"showtime"`
						Disabled      bool   `json:"disabled"`
						StandardPrice int    `json:"standard_price"`
					} `json:"sessions"`
				} `json:"formats"`
			} `json:"items"`
		} `json:"schedule"`
	} `json:"data"`
}

// parseCinemaStar разбирает ответ api.cinemastar.ru.
//
// Этот источник отдаёт всё окно сразу и параметр даты игнорирует — фильтрация
// по дате остаётся на нашей стороне.
func parseCinemaStar(body string) (Playbill, error) {
	var resp cinemaStarResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return Playbill{}, fmt.Errorf("разбор ответа Синема-Стар: %w", err)
	}

	pb := Playbill{
		Cinema: strings.TrimSpace(resp.Data.Theatre.Name),
		Dates:  resp.Data.Schedule.Dates,
	}
	for _, it := range resp.Data.Schedule.Items {
		for _, f := range it.Formats {
			for _, s := range f.Sessions {
				at := normalizeShowtime(s.Showtime, s.BusinessDate)
				if at == "" {
					continue
				}
				pb.Showtimes = append(pb.Showtimes, Showtime{
					Film:     strings.TrimSpace(it.Film.Name),
					StartsAt: at,
					Format:   f.Format,
					PriceMin: s.StandardPrice / 100,
					SourceID: fmt.Sprintf("%d", s.ID),
					OnSale:   !s.Disabled,
				})
			}
		}
	}
	return pb, nil
}

// normalizeShowtime приводит время сеанса к RFC3339.
// Источник может отдать полную дату-время или одно время — во втором случае
// дата берётся из business_date.
func normalizeShowtime(showtime, businessDate string) string {
	showtime = strings.TrimSpace(showtime)
	if showtime == "" {
		return ""
	}
	for _, layout := range []string{"2006-01-02 15:04:05", "2006-01-02T15:04:05", "2006-01-02 15:04"} {
		if t, err := time.ParseInLocation(layout, showtime, moscowTZ); err == nil {
			return t.Format(time.RFC3339)
		}
	}
	if len(showtime) == 5 && businessDate != "" {
		if at, err := joinDateTime(businessDate, showtime); err == nil {
			return at
		}
	}
	return ""
}

// ——— Kinoplan ———
//
// Общий движок касс для Космика, Киноквартала, Silver Cinema и Вики Синема.
// Важно не спутать с витриной «Кинокасса» из второго слоя: здесь запрос идёт к
// кассе конкретной площадки с её собственным токеном и отдаёт её расписание,
// а не городскую выгрузку.
//
// Обязательные заголовки (без x-platform отвечает 400 «Invalid headers»):
//
//	x-application-token: <токен площадки>
//	x-platform: widget
//	x-preferred-language: ru

type kinoplanResponse struct {
	Formats []struct {
		ID    int    `json:"id"`
		Title string `json:"title"`
	} `json:"formats"`
	Releases []struct {
		Title    string `json:"title"`
		Duration int    `json:"duration"`
		Seances  []struct {
			ID   string `json:"id"`
			Hall struct {
				Title string `json:"title"`
				IsVIP bool   `json:"is_vip"`
			} `json:"hall"`
			StartDateTime string `json:"start_date_time"`
			StartDate     string `json:"start_date"`
			Price         struct {
				Min int `json:"min"`
				Max int `json:"max"`
			} `json:"price"`
			AllowedOnlineSale bool `json:"is_allowed_online_sale"`
			Formats           []struct {
				Title string `json:"title"`
			} `json:"formats"`
		} `json:"seances"`
	} `json:"releases"`
}

// parseKinoplan разбирает ответ кассы Kinoplan.
//
// Здесь единственный из источников, где есть всё сразу: номер зала (отдельным
// объектом, а не строкой формата), вилка цены min/max, признак онлайн-продажи и
// хронометраж. Время приходит с зоной прямо в start_date_time.
//
// Цена в копейках, как у КАРО: 45000 — это 450 рублей.
func parseKinoplan(body string) (Playbill, error) {
	var resp kinoplanResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return Playbill{}, fmt.Errorf("разбор ответа Kinoplan: %w", err)
	}

	pb := Playbill{}
	seenDates := map[string]bool{}
	for _, r := range resp.Releases {
		for _, s := range r.Seances {
			at := parseZonedTime(s.StartDateTime)
			if at == "" {
				continue
			}
			if s.StartDate != "" && !seenDates[s.StartDate] {
				seenDates[s.StartDate] = true
				pb.Dates = append(pb.Dates, s.StartDate)
			}

			format := ""
			if len(s.Formats) > 0 {
				format = s.Formats[0].Title
			}
			// Признак VIP — про класс зала, а не про его номер, поэтому уезжает
			// в формат: в Hall остаётся только идентификатор помещения.
			if s.Hall.IsVIP {
				format = strings.TrimSpace(format + " VIP")
			}

			pb.Showtimes = append(pb.Showtimes, Showtime{
				Film:      strings.TrimSpace(r.Title),
				StartsAt:  at,
				Hall:      strings.TrimSpace(s.Hall.Title),
				Format:    format,
				PriceMin:  s.Price.Min / 100,
				PriceMax:  s.Price.Max / 100,
				SourceID:  s.ID,
				OnSale:    s.AllowedOnlineSale,
				DurationM: r.Duration,
			})
		}
	}
	return pb, nil
}

// parseZonedTime разбирает время, у которого зона уже указана источником.
func parseZonedTime(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	for _, layout := range []string{time.RFC3339, "2006-01-02T15:04:05.000-07:00", "2006-01-02T15:04:05-07:00"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.In(moscowTZ).Format(time.RFC3339)
		}
	}
	return ""
}
