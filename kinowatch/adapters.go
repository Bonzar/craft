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
	"html"
	"regexp"
	"sort"
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

	// LicenceID — номер прокатного удостоверения, если источник его отдаёт
	// (у Синема-Стар это government_code).
	//
	// Сам по себе он ничего не доказывает, но ОДНО удостоверение у двух разных
	// названий — прямая улика серого проката: показывают два разных фильма по
	// бумаге одной короткометражки. Замерено живьём 31.07: «Миньоны и монстры»
	// и «История игрушек 5» делят код 214004624, и обе идут под обёрткой
	// «Сказка на ночь» — той же, что Киномакс кладёт в фискальное название.
	//
	// В ключ сеанса и в отпечаток не входит: это свойство репертуара, а не
	// конкретного сеанса.
	LicenceID string `json:"licenceId,omitempty"`

	// Synopsis — описание позиции, если источник его отдаёт. Для каскада
	// матчинга это только бустер уверенности: описание бывает обрезано и
	// повторяется у фильмов одной серии, поэтому находкой само по себе не
	// является. В ключ сеанса и в отпечаток не входит — оно меняется.
	Synopsis string `json:"synopsis,omitempty"`
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
					Name     string `json:"name"`
					Duration int    `json:"duration"`
					// government_code приходит числом, но это идентификатор, а
					// не величина: json.Number бережёт его от потери точности
					// и от экспоненциальной записи.
					GovernmentCode json.Number `json:"government_code"`
					Description    string      `json:"description"`
				} `json:"film"`
				Formats []struct {
					// В группе формат приходит объектом {id, name}, а в самом
					// сеансе — строкой. Берём из сеанса: он ближе к факту и
					// избавляет от разбора двух форм одного поля.
					Sessions []struct {
						ID            int64  `json:"id"`
						BusinessDate  string `json:"business_date"`
						Showtime      string `json:"showtime"`
						Format        string `json:"format"`
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
					Format:   strings.TrimSpace(s.Format),
					PriceMin: s.StandardPrice / 100,
					SourceID: fmt.Sprintf("%d", s.ID),
					OnSale:   !s.Disabled,
					// Хронометраж и описание питают уровни каскада, а
					// удостоверение — уровень общего ПУ. Источник отдаёт всё
					// три, и терять их значило бы обеднить матчинг там, где
					// данных как раз хватает.
					DurationM: it.Film.Duration,
					LicenceID: strings.TrimSpace(it.Film.GovernmentCode.String()),
					Synopsis:  stripHTML(it.Film.Description),
				})
			}
		}
	}
	return pb, nil
}

// stripHTML вычищает разметку из описания.
//
// Синопсис приезжает готовым куском вёрстки («<p><span style=…>»), а каскаду
// нужен текст: по нему ищутся подсказки профиля, и «&nbsp;» между словами
// ломает поиск подстроки не хуже тега.
func stripHTML(s string) string {
	if s == "" {
		return ""
	}
	s = tagRe.ReplaceAllString(s, " ")
	s = html.UnescapeString(s)
	// Неразрывный пробел из &nbsp; остаётся отдельным символом и в обычный
	// пробел сам не превращается.
	s = strings.ReplaceAll(s, " ", " ")
	return strings.TrimSpace(multiSpace.ReplaceAllString(s, " "))
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

// ——— Москино ———
//
// Городская сеть, 21 площадка. Расписание отдаётся серверным HTML на странице
// площадки (`mos-kino.ru/cinema/<slug>/`) — все даты сразу, параметр даты
// страница не принимает.
//
// Особенность, ради которой здесь отдельный разбор дат: год в разметке не
// указан вовсе. Блок даты выглядит как «31 ИЮЛ (ПТ)», и превратить это в
// настоящую дату можно только относительно сегодняшнего дня. Отсюда опорная
// дата параметром: подставлять time.Now() внутри парсера значило бы сделать
// его непроверяемым и зависящим от часа прогона.
//
// Чего у источника нет: номера зала, хронометража и описания. Значит на этих
// площадках уровни каскада про длительность неприменимы — и это свойство
// источника, а не дефект разбора.

var (
	moskinoStep    = regexp.MustCompile(`(?s)<div class="step"[^>]*>(.*?)(?:<div class="step"|\z)`)
	moskinoDate    = regexp.MustCompile(`(?s)<div class="value">\s*(\d{1,2})\s+([А-ЯЁа-яё]+)`)
	moskinoItem    = regexp.MustCompile(`(?s)<div class="schedule-item">(.*?)</div>\s*</div>`)
	moskinoTitle   = regexp.MustCompile(`(?s)<div class="title">\s*(.*?)\s*</div>`)
	moskinoSession = regexp.MustCompile(`(?s)richSession\((\d+)\)(.*?)</a>`)
	moskinoTime    = regexp.MustCompile(`<span class="time">\s*([0-9]{1,2}:[0-9]{2})`)
	moskinoBadge   = regexp.MustCompile(`<span class="badge">\s*([^<]+?)\s*</span>`)
	moskinoPrice   = regexp.MustCompile(`<span class="price">\s*([0-9]+)`)
	moskinoName    = regexp.MustCompile(`(?s)<h1[^>]*>\s*(.*?)\s*</h1>`)
)

// moskinoMonths — сокращения месяцев так, как их пишет источник.
var moskinoMonths = map[string]time.Month{
	"янв": time.January, "фев": time.February, "мар": time.March,
	"апр": time.April, "май": time.May, "июн": time.June,
	"июл": time.July, "авг": time.August, "сен": time.September,
	"окт": time.October, "ноя": time.November, "дек": time.December,
}

// resolveMoskinoDate достраивает год к дате без года.
//
// Правило: год берётся такой, чтобы дата не оказалась в прошлом относительно
// опорной. Иначе декабрьское расписание, прочитанное в январе, уехало бы на год
// назад — и весь горизонт молча выпал бы из выдачи как «сеансы в прошлом».
func resolveMoskinoDate(day int, monthWord string, ref time.Time) (string, bool) {
	key := strings.ToLower(monthWord)
	if len([]rune(key)) > 3 {
		key = string([]rune(key)[:3])
	}
	month, ok := moskinoMonths[key]
	if !ok || day < 1 || day > 31 {
		return "", false
	}

	d := time.Date(ref.Year(), month, day, 0, 0, 0, 0, moscowTZ)
	// Запас в сутки: сеанс сегодняшнего дня уже мог начаться.
	if d.Before(ref.AddDate(0, 0, -1)) {
		d = d.AddDate(1, 0, 0)
	}
	return d.Format("2006-01-02"), true
}

// parseMoskino разбирает страницу площадки Москино.
//
// ref — опорная дата, относительно которой достраивается год.
func parseMoskino(body string, ref time.Time) (Playbill, error) {
	pb := Playbill{}
	if m := moskinoName.FindStringSubmatch(body); len(m) > 1 {
		pb.Cinema = strings.TrimSpace(tagRe.ReplaceAllString(m[1], ""))
	}

	steps := moskinoStep.FindAllStringSubmatch(body, -1)
	if len(steps) == 0 {
		// Ноль блоков дат при непустом теле — признак сменившейся вёрстки, а
		// не пустой афиши. Отличать это от «сеансов нет» обязан вызывающий:
		// иначе поломка разбора выглядела бы отсутствием фильма.
		return pb, fmt.Errorf("разбор Москино: блоки дат не найдены (тело %d байт)", len(body))
	}

	for _, step := range steps {
		block := step[1]

		dm := moskinoDate.FindStringSubmatch(block)
		if len(dm) < 3 {
			continue
		}
		day, err := strconv.Atoi(dm[1])
		if err != nil {
			continue
		}
		date, ok := resolveMoskinoDate(day, dm[2], ref)
		if !ok {
			continue
		}
		pb.Dates = append(pb.Dates, date)

		for _, item := range moskinoItem.FindAllStringSubmatch(block, -1) {
			tm := moskinoTitle.FindStringSubmatch(item[1])
			if len(tm) < 2 {
				continue
			}
			film := strings.TrimSpace(html.UnescapeString(tagRe.ReplaceAllString(tm[1], "")))

			for _, s := range moskinoSession.FindAllStringSubmatch(item[1], -1) {
				tail := s[2]
				tmm := moskinoTime.FindStringSubmatch(tail)
				if len(tmm) < 2 {
					continue
				}
				at := normalizeShowtime(tmm[1], date)
				if at == "" {
					continue
				}

				// Бейджей на сеансе бывает несколько: технология показа и
				// пометки вроде «СУБ». Все они про формат показа, а не про
				// помещение, поэтому едут в Format — Hall остаётся пустым.
				var badges []string
				for _, b := range moskinoBadge.FindAllStringSubmatch(tail, -1) {
					badges = append(badges, strings.TrimSpace(b[1]))
				}

				st := Showtime{
					Film:     film,
					StartsAt: at,
					Format:   strings.Join(badges, " "),
					SourceID: s[1],
					// Страница показывает только то, что продаётся: отдельного
					// признака доступности в разметке нет.
					OnSale: true,
				}
				if pm := moskinoPrice.FindStringSubmatch(tail); len(pm) > 1 {
					st.PriceMin, _ = strconv.Atoi(pm[1])
				}
				pb.Showtimes = append(pb.Showtimes, st)
			}
		}
	}

	return pb, nil
}

// ——— Mori Cinema ———
//
// HTML-страница расписания площадки (`mori.film/schedule/<id>`), серверный
// рендер Livewire. Важное про этот источник: расписания НЕТ ни на главной, ни
// на странице фильма — только на /schedule/<id>. Разведка этапа 2 говорила
// просто «HTML-страницы площадки», и это стоило одного лишнего круга.
//
// В блоке, который по классу называется «hall», лежит НЕ зал, а технология
// показа (2Д, ВИП 2Д, МАКС 2Д). Кладём в Format: номера зала источник не даёт
// вовсе, и подстановка формата в Hall сломала бы ключ сеанса.
//
// Хронометраж есть, но прозой — «3 часа 0 минут».

var (
	moriGroup   = regexp.MustCompile(`(?s)<div class="cinema__session-schedule__group">(.*?)(?:<div class="cinema__session-schedule__group">|\z)`)
	moriFilm    = regexp.MustCompile(`(?s)class="cinema__film__title[^"]*"[^>]*>\s*(.*?)\s*</a>`)
	moriParams  = regexp.MustCompile(`(?s)<div class="film-info__params">(.*?)</div>`)
	moriHall    = regexp.MustCompile(`(?s)<div class="cinema__session-schedule__item__hall">\s*(.*?)\s*</div>`)
	moriSession = regexp.MustCompile(`(?s)/session/(\d+)/buy"(.*?)</a>`)
	moriTime    = regexp.MustCompile(`__ticket__time">\s*([0-2]?\d:[0-5]\d)`)
	moriPrice   = regexp.MustCompile(`__ticket__price">\s*([0-9]+)`)
	// Части хронометража ищутся ПОРОЗНЬ. Одна регулярка с двумя опциональными
	// группами матчит пустую строку в самом начале текста и всегда возвращает
	// ноль — молча, без единой ошибки разбора.
	//
	// Окончания перечислены диапазоном, а не через `\w`: в RE2 `\w` — это
	// [0-9A-Za-z_], и «часа» с «минут» под него не подходят вовсе.
	moriHours   = regexp.MustCompile(`(\d+)\s*час[а-яё]*`)
	moriMinutes = regexp.MustCompile(`(\d+)\s*мин[а-яё]*`)
)

// parseRussianDuration разбирает хронометраж прозой: «3 часа 0 минут»,
// «1 час 47 минут», «107 минут». Ноль — законный исход «не сказано».
func parseRussianDuration(s string) int {
	total := 0
	if m := moriHours.FindStringSubmatch(s); len(m) > 1 {
		h, _ := strconv.Atoi(m[1])
		total += h * 60
	}
	if m := moriMinutes.FindStringSubmatch(s); len(m) > 1 {
		min, _ := strconv.Atoi(m[1])
		total += min
	}
	return total
}

// parseMori разбирает страницу расписания Mori.
//
// date — дата, за которую запрошена страница: в разметке её нет, страница
// всегда отдаёт выбранный день, а выбор задаётся параметром запроса.
func parseMori(body, date string) (Playbill, error) {
	pb := Playbill{}
	if date != "" {
		pb.Dates = []string{date}
	}

	groups := moriGroup.FindAllStringSubmatch(body, -1)
	if len(groups) == 0 {
		return pb, fmt.Errorf("разбор Mori: группы сеансов не найдены (тело %d байт)", len(body))
	}

	// Название и хронометраж стоят ПЕРЕД группами сеансов и относятся ко всем
	// группам до следующего фильма, поэтому идём по телу и запоминаем
	// последний встреченный фильм.
	for _, g := range groups {
		block := g[0]
		head := body[:strings.Index(body, block)+len(block)]

		film, dur := "", 0
		if fm := moriFilm.FindAllStringSubmatch(head, -1); len(fm) > 0 {
			film = strings.TrimSpace(html.UnescapeString(tagRe.ReplaceAllString(fm[len(fm)-1][1], "")))
		}
		if pm := moriParams.FindAllStringSubmatch(head, -1); len(pm) > 0 {
			dur = parseRussianDuration(stripHTML(pm[len(pm)-1][1]))
		}
		if film == "" {
			continue
		}

		format := ""
		if hm := moriHall.FindStringSubmatch(block); len(hm) > 1 {
			format = strings.TrimSpace(stripHTML(hm[1]))
		}

		for _, s := range moriSession.FindAllStringSubmatch(block, -1) {
			tm := moriTime.FindStringSubmatch(s[2])
			if len(tm) < 2 {
				continue
			}
			at := normalizeShowtime(tm[1], date)
			if at == "" {
				continue
			}

			st := Showtime{
				Film:     film,
				StartsAt: at,
				// Hall остаётся пустым намеренно: у Mori номера зала нет.
				Format:    format,
				SourceID:  s[1],
				OnSale:    true,
				DurationM: dur,
			}
			if pm := moriPrice.FindStringSubmatch(s[2]); len(pm) > 1 {
				st.PriceMin, _ = strconv.Atoi(pm[1])
			}
			pb.Showtimes = append(pb.Showtimes, st)
		}
	}
	return pb, nil
}

// ——— Пять звёзд ———
//
// HTML `5zvezd.ru/schedule/<slug>`, три московские площадки (Новокузнецкая,
// Павелецкая, Смоленская). Дата — параметром `?date=ДД.ММ.ГГГГ`.
//
// Единственный источник первого слоя, отдающий И номер зала, И хронометраж:
// зал лежит в title кнопки сеанса («Зал 5»), длительность — в подписи жанров
// («98 мин»). Цены нет вовсе — это свойство источника, а не пропуск разбора.

var (
	fiveItem     = regexp.MustCompile(`(?s)<div class="creation-schedule-item">(.*?)(?:<div class="creation-schedule-item">|\z)`)
	fiveTitle    = regexp.MustCompile(`(?s)<h2><a[^>]*>\s*(.*?)\s*</a></h2>`)
	fiveGenre    = regexp.MustCompile(`(?s)<div class="creation-genre">(.*?)</div>`)
	fiveMinutes  = regexp.MustCompile(`(\d+)\s*мин`)
	fiveCinema   = regexp.MustCompile(`(?s)<div class="cinema-name">\s*(.*?)\s*</div>`)
	fiveSession  = regexp.MustCompile(`(?s)<button[^>]*class="(session[^"]*)"[^>]*title="([^"]*)"[^>]*onclick="ticketManager\.session\(&#039;[^&]*&#039;,\s*(\d+)\);?"[^>]*>\s*([0-2]?\d:[0-5]\d)`)
	fiveHallNum  = regexp.MustCompile(`(?i)зал\s*([0-9A-Za-zА-Яа-я]+)`)
	fivePremium  = regexp.MustCompile(`(?s)<span class="session-vip"[^>]*>\s*([^<]+?)\s*</span>`)
	fiveSubtitle = regexp.MustCompile(`class="session-subtitle"`)
)

// parseFiveStars разбирает страницу расписания «Пяти звёзд».
func parseFiveStars(body, date string) (Playbill, error) {
	pb := Playbill{}
	if date != "" {
		pb.Dates = []string{date}
	}
	if cm := fiveCinema.FindStringSubmatch(body); len(cm) > 1 {
		pb.Cinema = strings.TrimSpace(stripHTML(cm[1]))
	}

	items := fiveItem.FindAllStringSubmatch(body, -1)
	if len(items) == 0 {
		return pb, fmt.Errorf("разбор «Пяти звёзд»: блоки фильмов не найдены (тело %d байт)", len(body))
	}

	for _, it := range items {
		block := it[1]

		tm := fiveTitle.FindStringSubmatch(block)
		if len(tm) < 2 {
			continue
		}
		film := strings.TrimSpace(html.UnescapeString(stripHTML(tm[1])))

		dur := 0
		if gm := fiveGenre.FindStringSubmatch(block); len(gm) > 1 {
			if mm := fiveMinutes.FindStringSubmatch(stripHTML(gm[1])); len(mm) > 1 {
				dur, _ = strconv.Atoi(mm[1])
			}
		}

		// Класс зала стоит ОТДЕЛЬНЫМ элементом сразу после кнопки сеанса,
		// поэтому привязывается по позиции в разметке, а не вложенностью.
		premium := map[int]string{}
		for _, pm := range fivePremium.FindAllStringSubmatchIndex(block, -1) {
			premium[pm[0]] = strings.TrimSpace(block[pm[2]:pm[3]])
		}

		for _, sm := range fiveSession.FindAllStringSubmatchIndex(block, -1) {
			classes := block[sm[2]:sm[3]]
			title := block[sm[4]:sm[5]]
			id := block[sm[6]:sm[7]]
			hhmm := block[sm[8]:sm[9]]

			at := normalizeShowtime(hhmm, date)
			if at == "" {
				continue
			}

			hall := ""
			if hm := fiveHallNum.FindStringSubmatch(title); len(hm) > 1 {
				hall = hm[1]
			}

			// Формат собирается из того, что стоит рядом: класс зала
			// (ПРЕМИУМ) и признак субтитров. Оба про услугу, не про помещение.
			var format []string
			for start, label := range premium {
				// Ярлык, идущий сразу за этой кнопкой и до следующей.
				if start >= sm[1] && start-sm[1] < 200 {
					format = append(format, label)
				}
			}
			if fiveSubtitle.MatchString(block[sm[1]:min(len(block), sm[1]+200)]) {
				format = append(format, "СУБ")
			}

			pb.Showtimes = append(pb.Showtimes, Showtime{
				Film:      film,
				StartsAt:  at,
				Hall:      hall,
				Format:    strings.Join(format, " "),
				SourceID:  id,
				DurationM: dur,
				// session-past — сеанс уже начался, купить нельзя.
				OnSale: !strings.Contains(classes, "session-past"),
			})
		}
	}
	return pb, nil
}

// ——— p24.app ———
//
// Движок, общий для Нивады («Премьер-Зал») и Колибри: страница площадки
// `<домен>/?date=ГГГГ/ММ/ДД&facility=<uuid>` отдаёт серверный HTML.
//
// Самый богатый источник первого слоя: даёт номер зала, цену, признак
// доступности, uuid сеанса и дату прямо в ссылке.
//
// Разметка — Next.js с CSS-модулями, поэтому имена классов несут хеш сборки
// («Show_show__kEocF») и меняются при каждом релизе фронта. Цепляться за них
// нельзя: разбор сломается молча в день чужого деплоя. Все зацепки ниже — за
// стабильные части: `data-uuid`, нехешированные дубли классов (`show-time`,
// `hall-name`, `facility-name`, `price`) и слово `disabled`.
var (
	p24Event    = regexp.MustCompile(`(?s)<div class="[^"]*event-info[^"]*">(.*?)(?:<div class="[^"]*event-info[^"]*">|\z)`)
	p24Title    = regexp.MustCompile(`(?s)<h2[^>]*>\s*<a[^>]*>\s*(.*?)\s*</a>`)
	p24Facility = regexp.MustCompile(`(?s)<span class="facility-name">\s*(.*?)\s*</span>`)
	p24Hall     = regexp.MustCompile(`(?s)<span class="hall-name">\s*(.*?)\s*</span>(.*?)(?:<span class="hall-name">|\z)`)
	p24Show     = regexp.MustCompile(`(?s)<div class="([^"]*\bshow\b[^"]*)">(.*?)(?:<div class="[^"]*\bshow\b[^"]*">|\z)`)
	p24UUID     = regexp.MustCompile(`data-uuid="([0-9a-f-]{8,})"`)
	p24Time     = regexp.MustCompile(`show-time[^>]*>\s*([0-2]?\d:[0-5]\d)`)
	p24Date     = regexp.MustCompile(`[?&]date=(\d{4})/(\d{2})/(\d{2})`)
	p24Price    = regexp.MustCompile(`price[^>]*>\s*([0-9]+)`)
	p24Formats  = regexp.MustCompile(`(?s)formats[^>]*>(.*?)</div>`)
	p24HallNum  = regexp.MustCompile(`(?i)зал\s*([0-9A-Za-zА-Яа-я]+)`)
)

// parseP24 разбирает страницу площадки на движке p24.app.
//
// fallbackDate используется, только если в ссылке сеанса даты не оказалось:
// собственная дата ссылки точнее переданной, потому что страница может отдать
// соседний день.
func parseP24(body, fallbackDate string) (Playbill, error) {
	pb := Playbill{}
	if fm := p24Facility.FindStringSubmatch(body); len(fm) > 1 {
		pb.Cinema = strings.TrimSpace(stripHTML(fm[1]))
	}

	events := p24Event.FindAllStringSubmatch(body, -1)
	if len(events) == 0 {
		return pb, fmt.Errorf("разбор p24: блоки фильмов не найдены (тело %d байт)", len(body))
	}

	dates := map[string]bool{}
	for _, ev := range events {
		block := ev[1]

		tm := p24Title.FindStringSubmatch(block)
		if len(tm) < 2 {
			continue
		}
		film := strings.TrimSpace(html.UnescapeString(stripHTML(tm[1])))

		for _, hm := range p24Hall.FindAllStringSubmatch(block, -1) {
			hallLabel, hallBlock := hm[1], hm[2]

			// «Зал 1 (кровати)» — в Hall едет только номер: описание зала это
			// про удобства, а не про идентификатор помещения.
			hall := ""
			if n := p24HallNum.FindStringSubmatch(hallLabel); len(n) > 1 {
				hall = n[1]
			}

			for _, sm := range p24Show.FindAllStringSubmatch(hallBlock, -1) {
				classes, show := sm[1], sm[2]

				tmm := p24Time.FindStringSubmatch(show)
				if len(tmm) < 2 {
					continue
				}

				date := fallbackDate
				if dm := p24Date.FindStringSubmatch(show); len(dm) > 3 {
					date = dm[1] + "-" + dm[2] + "-" + dm[3]
				}
				at := normalizeShowtime(tmm[1], date)
				if at == "" {
					continue
				}
				if date != "" {
					dates[date] = true
				}

				st := Showtime{
					Film:     film,
					StartsAt: at,
					Hall:     hall,
					// disabled — сеанс в расписании есть, купить нельзя.
					OnSale: !strings.Contains(classes, "disabled"),
				}
				if um := p24UUID.FindStringSubmatch(show); len(um) > 1 {
					st.SourceID = um[1]
				}
				if pm := p24Price.FindStringSubmatch(show); len(pm) > 1 {
					st.PriceMin, _ = strconv.Atoi(pm[1])
				}
				if fm := p24Formats.FindStringSubmatch(show); len(fm) > 1 {
					st.Format = strings.TrimSpace(stripHTML(fm[1]))
				}
				pb.Showtimes = append(pb.Showtimes, st)
			}
		}
	}

	for d := range dates {
		pb.Dates = append(pb.Dates, d)
	}
	sort.Strings(pb.Dates)
	return pb, nil
}
