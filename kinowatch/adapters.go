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
	kindCinemaPark = "cinemapark" // kinoteatr.ru, JSON-обёртка над HTML
	kindKinoplan   = "kinoplan"   // kinokassa.kinoplan24.ru, токен выводится из виджета
	kindMoskino    = "moskino"    // HTML mos-kino.ru
	kindMori       = "mori"       // HTML mori.film
	kind5Zvezd     = "5zvezd"     // HTML 5zvezd.ru
	kindP24        = "p24"        // движок p24.app, у каждой площадки свой домен
	kindPushka     = "pushka"     // JSON cinema.pushka.club, площадка в куке

	// Одиночки со своим сайтом: движок общим ни с кем не является, поэтому вид
	// канала совпадает с самой площадкой, а параметр ей не нужен вовсе.
	kindHudozhestvenny = "hudozhestvenny" // HTML cinema1909.ru
	kindGum            = "gum"            // HTML gum.ru/kinozal

	// Движки, найденные позже: у каждого свой сайт на площадку или общая
	// страница города с отбором внутри.
	kindPremierzal = "premierzal" // HTML сети «Премьер-Зал», домен на площадку
	kindMirage     = "mirage"     // HTML mirage.ru, одна страница на все площадки города
	kindCinema5    = "cinema5"    // JSON cinema5.ru, горизонт у источника два дня
	kindEtobilet   = "etobilet"   // движок etobilet, расписание JSON-ом внутри страницы

	// Одиночки со своим сайтом и своей разметкой — движок ни с кем не общий.
	kindPioner    = "pioner"    // HTML pioner-cinema.ru
	kindPoklonka  = "poklonka"  // HTML poklonka-cinema.ru, весь горизонт разом
	kindMoskva    = "moskva"    // HTML cinema.moscow
	kindRomanov   = "romanov"   // POST-API, серверный HTML — пустой шаблон
	kindAlmaz     = "almaz"     // HTML almazcinema.com, сеанс JSON-ом в атрибуте
	kindIllusion  = "illuzion"  // HTML illusion-cinema.ru, весь горизонт разом
	kindLuxor     = "luxor"     // HTML luxorfilm.ru, сеансы массивом filmsAll
	kindMosfilm   = "mosfilm"   // HTML centerkino.mosfilm.ru
	kindTretyakov = "tretyakov" // HTML tretyakovgallery.ru, отбор по корпусу
	kindJewish    = "jewish"    // HTML jewish-museum.ru, кино среди прочих событий
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
//
// Для этих площадок Kinoplan — ПЕРВЫЙ слой, и критерий тут не в том, кому
// принадлежит домен, а в том, есть ли у площадки другой канал продаж. У них
// другого нет: собственные сайты отдают лишь загрузчик виджета без единого
// времени сеанса. Значит эта касса и есть их собственная — единственная, через
// которую вообще продаются билеты.
//
// Важно не спутать с витриной «Кинокасса»: та отдаёт городскую выгрузку по
// всем площадкам вендора и остаётся вторым слоем. Здесь запрос идёт к кассе
// конкретной площадки и возвращает её расписание.
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
			ID string `json:"id"`
			// CinemaID — чья это площадка. Ответ кассы содержит сеансы ВСЕХ
			// площадок приложения: у Киноквартала одно приложение на Ясенево и
			// Варшавский, и без этого поля каждая получала бы расписание обеих.
			CinemaID int `json:"cinema_id"`
			Hall     struct {
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
	return parseKinoplanFor(body, 0)
}

// parseKinoplanFor оставляет сеансы одной площадки.
//
// Касса отвечает афишей всего приложения, а приложение бывает общим на
// несколько кинотеатров — замерено живьём: ответ приложения Киноквартала несёт
// 22 сеанса Варшавского и 17 Ясенева вперемешку. Без отбора обе площадки
// получили бы 39 чужих сеансов вдобавок к своим, и выглядело бы это как их
// собственное расписание.
//
// cinemaID = 0 означает «не отбирать»: у приложения на одну площадку отбирать
// нечего, и требовать идентификатор там незачем.
func parseKinoplanFor(body string, cinemaID int) (Playbill, error) {
	var resp kinoplanResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return Playbill{}, fmt.Errorf("разбор ответа Kinoplan: %w", err)
	}

	pb := Playbill{}
	seenDates := map[string]bool{}
	for _, r := range resp.Releases {
		for _, s := range r.Seances {
			if cinemaID != 0 && s.CinemaID != 0 && s.CinemaID != cinemaID {
				continue
			}
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

// moriEmptyDayMarker — чем Mori сам говорит, что на выбранную дату сеансов нет.
//
// Маркер стоит внутри живого контейнера расписания, поэтому и служит границей
// между «сеансов нет» и «вёрстка сменилась»: без него пустой разбор остаётся
// поломкой источника.
const moriEmptyDayMarker = "Нет сеансов на выбранную дату"

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
	// Часы: «2 часа», «1 ч. 38 мин.» (Mori), «1 ч 44 мин» (кинотеатр «Москва»).
	//
	// Точка после «ч» необязательна, а вот отделить её от кириллического слова
	// приходится классом символов, а НЕ границей `\b`: в Go граница слова
	// считается по ASCII, и рядом с кириллической «ч» она не срабатывает
	// вовсе. С `\b` часы молча терялись у всех источников сразу — фильм на
	// 1 ч 38 мин приезжал как 38-минутный, то есть уровень каскада про
	// аномальную длительность получал ложный вход и мог принять полнометражку
	// за короткометражку-обёртку.
	moriHours   = regexp.MustCompile(`(\d+)\s*(?:час[а-яё]*|ч(?:[.,)\s]|$))`)
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
		// Пустой день источник помечает сам: контейнер расписания на месте, а
		// внутри стоит блок «Нет сеансов на выбранную дату». Это ответ кассы, а
		// не поломка, и путать их нельзя — иначе площадка, у которой сеансы
		// сегодня уже кончились, объявляется мёртвой.
		if strings.Contains(body, moriEmptyDayMarker) {
			return pb, nil
		}
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

		// Разбивка по залам у движка НЕОБЯЗАТЕЛЬНА. У Колибри блоки залов есть,
		// у «Часа кино» их нет вовсе — сеансы лежат прямо в блоке фильма. Пока
		// зал считался обязательным, площадка без него отдавала пустую афишу
		// при HTTP 200: канал выглядел живым и молчащим, а на деле разбор
		// искал разметку, которой у этого сайта не бывает.
		halls := p24Hall.FindAllStringSubmatch(block, -1)
		if len(halls) == 0 {
			// Зала нет — вся группа сеансов идёт без номера помещения. Пустой
			// Hall честнее выдуманного: он участвует в ключе сеанса.
			halls = [][]string{{"", "", block}}
		}

		for _, hm := range halls {
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

// ——— СИНЕМА ПАРК / Формула Кино ———
//
// `kinoteatr.ru/raspisanie-kinoteatrov/<slug>/?date=&ajax=1` отдаёт JSON, внутри
// которого лежит кусок HTML. Разбираем оба слоя: сперва конверт, потом разметку.
//
// ПРЕДУСЛОВИЕ: с иностранного адреса хост рвёт соединение — не 403, а обрыв,
// который выглядит как «сайт лежит». Нужен российский выход (tunnel.go).
//
// Про зал: источник даёт класс обслуживания («Мувик», «Комфорт»), а не номер
// помещения. Он едет в Format вместе с технологией показа; Hall остаётся пустым,
// и два сеанса одного фильма в один час различаются своим openWidget-id.
var (
	cpMovie   = regexp.MustCompile(`(?s)movie_card_header[^>]*>\s*(.*?)\s*</span>(.*?)(?:movie_card_header|\z)`)
	cpRuntime = regexp.MustCompile(`(?s)<span class="title">\s*([^<]*?мин[^<]*?)\s*</span>`)
	cpSession = regexp.MustCompile(`(?s)openWidget=(\d+)"(.*?)</a>`)
	cpTime    = regexp.MustCompile(`shedule_session_time">\s*([0-2]?\d:[0-5]\d)`)
	cpPrice   = regexp.MustCompile(`shedule_session_price">\s*(?:от\s*)?([0-9]+)`)
	cpFormat  = regexp.MustCompile(`shedule_session_format">\s*([^<]+?)\s*</span>`)
	cpDate    = regexp.MustCompile(`[?&]date=(\d{4}-\d{2}-\d{2})`)
	cpTitle   = regexp.MustCompile(`«([^»]+)»`)
)

type cinemaParkResponse struct {
	Content string `json:"content"`
	Title   string `json:"title"`
}

// parseCinemaPark разбирает ajax-ответ kinoteatr.ru.
func parseCinemaPark(body, fallbackDate string) (Playbill, error) {
	var resp cinemaParkResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return Playbill{}, fmt.Errorf("разбор конверта СИНЕМА ПАРК: %w", err)
	}
	if strings.TrimSpace(resp.Content) == "" {
		return Playbill{}, fmt.Errorf("разбор СИНЕМА ПАРК: пустой content в ответе")
	}

	pb := Playbill{}
	// Название площадки живёт в заголовке страницы, в кавычках-ёлочках:
	// «Расписание кинотеатра на сегодня «Формула Кино ЦДМ (Москва)» …».
	if tm := cpTitle.FindStringSubmatch(resp.Title); len(tm) > 1 {
		pb.Cinema = strings.TrimSpace(tm[1])
	}

	movies := cpMovie.FindAllStringSubmatch(resp.Content, -1)
	if len(movies) == 0 {
		return pb, fmt.Errorf("разбор СИНЕМА ПАРК: блоки фильмов не найдены (content %d байт)", len(resp.Content))
	}

	dates := map[string]bool{}
	for _, mv := range movies {
		film := strings.TrimSpace(html.UnescapeString(stripHTML(mv[1])))
		block := mv[2]
		if film == "" {
			continue
		}

		dur := 0
		if rm := cpRuntime.FindStringSubmatch(block); len(rm) > 1 {
			dur = parseRussianDuration(rm[1])
		}

		for _, s := range cpSession.FindAllStringSubmatch(block, -1) {
			id, tail := s[1], s[2]

			tm := cpTime.FindStringSubmatch(tail)
			if len(tm) < 2 {
				continue
			}

			date := fallbackDate
			if dm := cpDate.FindStringSubmatch(s[0]); len(dm) > 1 {
				date = dm[1]
			}
			at := normalizeShowtime(tm[1], date)
			if at == "" {
				continue
			}
			if date != "" {
				dates[date] = true
			}

			// Форматов у сеанса бывает несколько: технология и класс зала
			// («2D» плюс «Мувик»). Оба про услугу, оба в Format.
			var formats []string
			for _, fm := range cpFormat.FindAllStringSubmatch(tail, -1) {
				if v := strings.TrimSpace(stripHTML(fm[1])); v != "" {
					formats = append(formats, v)
				}
			}

			st := Showtime{
				Film:     film,
				StartsAt: at,
				// Hall пустой: номера зала источник не даёт, только класс.
				Format:    strings.Join(formats, " "),
				SourceID:  id,
				OnSale:    true,
				DurationM: dur,
			}
			if pm := cpPrice.FindStringSubmatch(tail); len(pm) > 1 {
				st.PriceMin, _ = strconv.Atoi(pm[1])
			}
			pb.Showtimes = append(pb.Showtimes, st)
		}
	}

	for d := range dates {
		pb.Dates = append(pb.Dates, d)
	}
	sort.Strings(pb.Dates)
	return pb, nil
}

// ——— Pushka ———
//
// Единственный JSON среди одиночек и самый богатый по полям: цена, номер зала,
// признак доступности и хронометраж.
//
// Главная особенность — выбор площадки. `/!/ajax/schedule` отдаёт расписание
// той площадки, чей идентификатор лежит в куке `cinema_id`; ставит эту куку
// страница площадки. Ни путь `/moscow/<slug>/!/ajax/schedule`, ни Referer, ни
// query-параметры на выдачу не влияют — проверено живьём всеми тремя способами,
// и голый запрос молча возвращает дефолтный «Клён». Поэтому клиент обязан быть
// сессионным (newSessionClient), а сбор идёт по одной площадке за раз.

// pushkaVenues — московские площадки Pushka. Слаг совпадает с сегментом её
// страницы, и он же — ключ к куке.
var pushkaVenues = []string{"klen", "ladya", "key"}

const pushkaBase = "https://cinema.pushka.club"

type pushkaResponse struct {
	Dates struct {
		Today string `json:"today"`
	} `json:"dates"`
	Title string `json:"title"`
	// schedule: дата → позиции, у каждой film_id и сеансы по форматам.
	Schedule map[string][]struct {
		FilmID    int `json:"film_id"`
		Showtimes map[string][]struct {
			ID          int64  `json:"id"`
			Time        string `json:"time"`
			Date        string `json:"date"`
			IsAvailable bool   `json:"is_available"`
			Price       int    `json:"price"`
			HallID      int    `json:"hall_id"`
		} `json:"showtimes"`
	} `json:"schedule"`
	// films: film_id строкой → карточка фильма.
	Films map[string]struct {
		Name     string `json:"name"`
		Duration int    `json:"duration"`
		URL      string `json:"url"`
	} `json:"films"`
}

// parsePushka разбирает расписание одной площадки Pushka.
func parsePushka(body string) (Playbill, error) {
	var resp pushkaResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return Playbill{}, fmt.Errorf("разбор ответа Pushka: %w", err)
	}
	if len(resp.Schedule) == 0 {
		return Playbill{}, fmt.Errorf("разбор Pushka: расписание пусто (тело %d байт)", len(body))
	}

	pb := Playbill{Cinema: strings.TrimSpace(resp.Title)}

	dates := make([]string, 0, len(resp.Schedule))
	for d := range resp.Schedule {
		dates = append(dates, d)
	}
	// Порядок ключей map случайный, а выдача обязана быть предсказуемой:
	// иначе два прогона дали бы один набор сеансов в разном порядке.
	sort.Strings(dates)
	pb.Dates = dates

	for _, date := range dates {
		for _, pos := range resp.Schedule[date] {
			film := resp.Films[strconv.Itoa(pos.FilmID)]
			// Названия нет в словаре — оставляем пустым. Подставить соседний
			// фильм значило бы создать ложную находку.
			name := strings.TrimSpace(film.Name)

			formats := make([]string, 0, len(pos.Showtimes))
			for f := range pos.Showtimes {
				formats = append(formats, f)
			}
			sort.Strings(formats)

			for _, format := range formats {
				for _, s := range pos.Showtimes[format] {
					at := normalizeShowtime(s.Date, date)
					if at == "" {
						at = normalizeShowtime(s.Time, date)
					}
					if at == "" {
						continue
					}

					pb.Showtimes = append(pb.Showtimes, Showtime{
						Film:     name,
						StartsAt: at,
						// Номер зала источник отдаёт числом.
						Hall:      strconv.Itoa(s.HallID),
						Format:    format,
						PriceMin:  s.Price,
						SourceID:  strconv.FormatInt(s.ID, 10),
						OnSale:    s.IsAvailable,
						DurationM: film.Duration,
						DeepLink:  strings.TrimSpace(film.URL),
					})
				}
			}
		}
	}
	return pb, nil
}

// fetchPushkaVenue тянет расписание одной площадки.
//
// Два запроса подряд ОДНИМ сессионным клиентом: страница площадки ставит куку
// `cinema_id`, ajax её использует. Клиент без банки вернул бы дефолтную
// площадку, и это молчаливая подмена — ответ приходит валидный, просто не тот.
func fetchPushkaVenue(c *Client, slug string) (Playbill, error) {
	if _, err := c.getText(pushkaBase + "/moscow/" + slug); err != nil {
		return Playbill{}, fmt.Errorf("страница площадки %q: %w", slug, err)
	}
	body, err := c.getText(pushkaBase + "/!/ajax/schedule")
	if err != nil {
		return Playbill{}, fmt.Errorf("расписание площадки %q: %w", slug, err)
	}
	return parsePushka(body)
}

// ——— Художественный ———
//
// `cinema1909.ru/schedule/<ГГГГ-ММ-ДД>` — страница одной даты. Горизонт больше
// трёх недель, но каждая дата запрашивается отдельно: параметр `?date=` сайт
// игнорирует и молча отдаёт сегодняшний день, а путь с датой работает.
//
// Самый полный источник среди одиночек: номер зала, цена, хронометраж прозой
// («2 часа 7 минут») и язык показа.
//
// Разбор идёт по данным страницы, а не по её вёрстке. Сайт — Next.js, и всё
// расписание уже лежит в теле готовым JSON-ом (`__NEXT_DATA__`): название,
// хронометраж, время сеанса, зал, цена и признак продажи. Прежняя версия
// цеплялась за имена CSS-модулей и перестала находить карточки на первом же
// релизе фронта — замерено живьём: страница отдаёт 200 и 67 КБ, а сеансов ноль.
//
// Данные страницы устойчивее её оформления: классы несут хеш сборки и меняются
// каждым деплоем, а имена полей — часть модели, которую фронт сам и читает.
var hudNextData = regexp.MustCompile(`(?s)<script id="__NEXT_DATA__"[^>]*>(.*?)</script>`)

type hudPage struct {
	Props struct {
		PageProps struct {
			Data struct {
				Events []struct {
					Type      string `json:"type"`
					Title     string `json:"title"`
					Slug      string `json:"slug"`
					Duration  int    `json:"duration"`
					Showtimes []struct {
						Datetime string `json:"datetime"`
						Note     string `json:"note"`
						Price    int    `json:"price"`
						Location struct {
							Title string `json:"title"`
						} `json:"location"`
						IsSaleAvailable bool `json:"isSaleAvailable"`
					} `json:"showtimes"`
				} `json:"events"`
			} `json:"data"`
		} `json:"pageProps"`
	} `json:"props"`
}

// parseHudozhestvenny разбирает страницу расписания на одну дату.
func parseHudozhestvenny(body, date string) (Playbill, error) {
	pb := Playbill{Cinema: "Художественный"}
	if date != "" {
		pb.Dates = []string{date}
	}

	m := hudNextData.FindStringSubmatch(body)
	if len(m) < 2 {
		return pb, fmt.Errorf("разбор Художественного: данные страницы не найдены (тело %d байт)", len(body))
	}

	var page hudPage
	if err := json.Unmarshal([]byte(m[1]), &page); err != nil {
		return pb, fmt.Errorf("разбор Художественного: %w", err)
	}

	for _, e := range page.Props.PageProps.Data.Events {
		// В афише кинотеатра бывают не только фильмы (лекции, встречи). Тип
		// события отдаёт сам источник, и гадать по названию незачем.
		if e.Type != "MOVIE" {
			continue
		}
		film := strings.TrimSpace(e.Title)
		if film == "" {
			continue
		}

		for _, sh := range e.Showtimes {
			at := parseZonedTime(sh.Datetime)
			if at == "" {
				continue
			}
			pb.Showtimes = append(pb.Showtimes, Showtime{
				Film:     film,
				StartsAt: at,
				Hall:     strings.TrimSpace(sh.Location.Title),
				// Пометка про язык показа — про услугу, а не про зал.
				Format:    strings.TrimSpace(sh.Note),
				PriceMin:  sh.Price,
				DurationM: e.Duration,
				OnSale:    sh.IsSaleAvailable,
				// Своего идентификатора сеанса источник не даёт — в реестре
				// такие сеансы различаются отпечатком. Слаг фильма сюда не
				// годится: он один на все сеансы дня.
				DeepLink: "https://cinema1909.ru/movies/" + e.Slug,
			})
		}
	}

	// Ни одного сеанса при полученных данных — тоже поломка разбора: у страницы
	// даты сеансы есть всегда, иначе её бы не отдали.
	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Художественного: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// ——— ГУМ ———
//
// `gum.ru/kinozal/` — расписание кинозала. Важно не перепутать с главной
// страницей `gum.ru`: там времена есть, но это часы работы торгового центра
// («ежедневно с 10:00 до 22:00»), а не сеансы.
//
// Даты переключаются выпадающим списком, где значение опции — идентификатор
// дня; человекочитаемая подпись стоит рядом («Понедельник 31 Августа»).
var (
	gumItem   = regexp.MustCompile(`(?s)<div class="kino__item">(.*?)(?:<div class="kino__item">|\z)`)
	gumTitle  = regexp.MustCompile(`(?s)kino__title"[^>]*>\s*<a[^>]*href="/kinozal/movie/id/(\d+)/"[^>]*>\s*(.*?)\s*</a>`)
	gumTime   = regexp.MustCompile(`ticketManager\.session\("([^"]+)",\s*(\d+)\);'>\s*([0-2]?\d:[0-5]\d)`)
	gumDayOpt = regexp.MustCompile(`<option value="(\d+)"[^>]*>\s*([^<]+?)\s*</option>`)
)

// parseGum разбирает расписание кинозала ГУМа.
func parseGum(body, date string) (Playbill, error) {
	pb := Playbill{Cinema: "ГУМ Кинозал"}
	if date != "" {
		pb.Dates = []string{date}
	}

	items := gumItem.FindAllStringSubmatch(body, -1)
	if len(items) == 0 {
		return pb, fmt.Errorf("разбор ГУМа: карточки фильмов не найдены (тело %d байт)", len(body))
	}

	for _, it := range items {
		block := it[1]

		tm := gumTitle.FindStringSubmatch(block)
		if len(tm) < 3 {
			continue
		}
		film := strings.TrimSpace(html.UnescapeString(stripHTML(tm[2])))
		if film == "" {
			continue
		}

		for _, s := range gumTime.FindAllStringSubmatch(block, -1) {
			at := normalizeShowtime(s[3], date)
			if at == "" {
				continue
			}
			pb.Showtimes = append(pb.Showtimes, Showtime{
				Film:     film,
				StartsAt: at,
				// Второй аргумент ticketManager.session — идентификатор сеанса,
				// первый описывает площадку и у всех сеансов один.
				SourceID: s[2],
				OnSale:   true,
				DeepLink: "https://gum.ru/kinozal/movie/id/" + tm[1] + "/",
			})
		}
	}
	return pb, nil
}

// gumDays — идентификаторы дат из выпадающего списка страницы.
// Нужны, чтобы обойти весь горизонт, а не только сегодняшний день.
func gumDays(body string) map[string]string {
	out := map[string]string{}
	for _, m := range gumDayOpt.FindAllStringSubmatch(body, -1) {
		label := strings.TrimSpace(stripHTML(m[2]))
		if label != "" {
			out[m[1]] = label
		}
	}
	return out
}

// ——— Премьерзал ———
//
// Движок сети «Премьер-Зал». Найден по виджету `widget.premierzal.ru` на сайте
// площадки — прежняя разведка ошибочно записала эти кинотеатры в p24, хотя
// facility-uuid в их разметке нет вовсе.
//
// Площадка задаётся своим доменом: движок общий, сайт у каждой свой. Страница
// расписания отдаёт один день — сегодняшний; переключателя даты в разметке нет,
// параметр `date` страница не принимает.
//
// Разметка смысловая и без хешей сборки, поэтому зацепки за неё устойчивы.
// Отдельного упоминания стоит прошедший сеанс: источник помечает его
// модификатором `_passed`, и это прямой признак «билетов уже не купить» — тот
// самый OnSale, который у большинства источников приходится выводить косвенно.
var (
	pzFilmName = regexp.MustCompile(`^\s*([^<]+?)\s*</div>`)
	pzFormat   = regexp.MustCompile(`schedule__session-format">\s*([^<]*?)\s*<`)
	pzTime     = regexp.MustCompile(`>\s*([0-2]?\d:[0-5]\d)\s*<`)
	pzPrice    = regexp.MustCompile(`session-picker__item-price">[^<0-9]*(\d[\d\s]*)`)
)

// parsePremierzal разбирает страницу расписания площадки Премьерзала.
//
// Блоки режутся разбиением, а не регулярным выражением с «до следующего»:
// нежадный поиск съедает разделитель и теряет каждый второй блок. Здесь это
// стоило бы всех фильмов кроме первого — тест поймал ровно это.
func parsePremierzal(body, date string) (Playbill, error) {
	pb := Playbill{}
	if date != "" {
		pb.Dates = []string{date}
	}

	films := strings.Split(body, `schedule__film-name">`)
	if len(films) < 2 {
		return pb, fmt.Errorf("разбор Премьерзала: фильмы не найдены (тело %d байт)", len(body))
	}

	for _, chunk := range films[1:] {
		nm := pzFilmName.FindStringSubmatch(chunk)
		if len(nm) < 2 {
			continue
		}
		film := strings.TrimSpace(html.UnescapeString(nm[1]))
		if film == "" {
			continue
		}

		format := ""
		if fm := pzFormat.FindStringSubmatch(chunk); len(fm) > 1 {
			format = strings.TrimSpace(fm[1])
		}

		// Каждый кусок начинается со списка классов самого сеанса — именно там
		// стоит пометка о том, что сеанс уже прошёл.
		for _, item := range strings.Split(chunk, `class="schedule__session-time `)[1:] {
			tm := pzTime.FindStringSubmatch(item)
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
				Format:   format,
				// Прошедший сеанс источник помечает сам — это его собственное
				// слово о продаже, а не наш вывод по времени.
				OnSale: !strings.Contains(item[:min(len(item), 200)], "session-picker__item_passed"),
			}
			if pm := pzPrice.FindStringSubmatch(item); len(pm) > 1 {
				st.PriceMin, _ = strconv.Atoi(strings.Join(strings.Fields(pm[1]), ""))
			}
			pb.Showtimes = append(pb.Showtimes, st)
		}
	}

	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Премьерзала: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// ——— Мираж ———
//
// Сеть считалась SPA без собственного канала — это оказалось неверно: сайт
// найден по тегу website в OSM, и расписание отдаётся сервером обычным HTML.
//
// Адрес расписания — свой у каждой площадки: `/msk/schedule/cinema/<id>/`.
// Общая страница города `/msk/schedule/` выглядит как список всех, но отдаёт
// только выбранную по умолчанию — замерено живьём: один блок площадки, всегда
// MARI, при любом параметре кинотеатра и любой куке. Взять её значило бы выдать
// трём площадкам одно расписание.
//
// Отбор по блоку площадки сохранён и на этом адресе: он дешёвый, а промах по
// идентификатору отличает от пустого расписания.
var (
	mirageBox   = regexp.MustCompile(`(?s)<div class="session-box">(.*?)(?:<div class="session-box">|\z)`)
	mirageVenue = regexp.MustCompile(`md-title">\s*<a href="/msk/cinema/(\d+)/?"`)
	mirageItem  = regexp.MustCompile(`(?s)<div class="title">\s*(.*?)\s*</div>(.*?)(?:<div class="title">|\z)`)
	mirageHall  = regexp.MustCompile(`<span class="blue">\s*([^<]+?)\s*</span>`)
	mirageTime  = regexp.MustCompile(`<div class="time">\s*([0-2]?\d:[0-5]\d)\s*</div>`)
	mirageFmt   = regexp.MustCompile(`<div class="format">\s*([^<]*?)\s*</div>`)
	mirageLink  = regexp.MustCompile(`href="(/ticket_new/[0-9a-f-]+/?)"`)
)

// parseMirage разбирает расписание Москвы, оставляя сеансы одной площадки.
//
// venue — числовой идентификатор кинотеатра в адресе его страницы. Пустой
// означает «взять всё»: у сети из одной площадки отбирать нечего.
func parseMirage(body, venue, date string) (Playbill, error) {
	pb := Playbill{}
	if date != "" {
		pb.Dates = []string{date}
	}

	boxes := mirageBox.FindAllStringSubmatch(body, -1)
	if len(boxes) == 0 {
		return pb, fmt.Errorf("разбор Миража: блоки площадок не найдены (тело %d байт)", len(body))
	}

	seen := false
	for _, box := range boxes {
		vm := mirageVenue.FindStringSubmatch(box[1])
		if len(vm) < 2 {
			continue
		}
		if venue != "" && vm[1] != venue {
			continue
		}
		seen = true

		for _, it := range mirageItem.FindAllStringSubmatch(box[1], -1) {
			film := strings.TrimSpace(html.UnescapeString(stripHTML(it[1])))
			tm := mirageTime.FindStringSubmatch(it[2])
			if film == "" || len(tm) < 2 {
				continue
			}
			at := normalizeShowtime(tm[1], date)
			if at == "" {
				continue
			}

			st := Showtime{Film: film, StartsAt: at, OnSale: true}
			if hm := mirageHall.FindStringSubmatch(it[2]); len(hm) > 1 {
				st.Hall = strings.TrimSpace(hm[1])
			}
			if fm := mirageFmt.FindStringSubmatch(it[2]); len(fm) > 1 {
				st.Format = strings.TrimSpace(fm[1])
			}
			if lm := mirageLink.FindStringSubmatch(it[2]); len(lm) > 1 {
				st.DeepLink = "https://mirage.ru" + lm[1]
				// Идентификатор сеанса у источника есть — это uuid в ссылке на
				// билет. Он различает два сеанса одного фильма в один час.
				st.SourceID = strings.Trim(strings.TrimPrefix(lm[1], "/ticket_new/"), "/")
			}
			pb.Showtimes = append(pb.Showtimes, st)
		}
	}

	// Площадки нет на странице — это не «сеансов нет», а промах по
	// идентификатору, и путать эти вещи нельзя.
	if venue != "" && !seen {
		return pb, fmt.Errorf("разбор Миража: площадки %q нет на странице расписания", venue)
	}
	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Миража: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// ——— СИНЕМА 5 ———
//
// `cinema5.ru/api/v1/movies/page/<страница>?cinemaIds=<id>` отдаёт афишу
// площадки готовым JSON. Отбор по площадке делает сам сервис: 21 → 6 сеансов,
// 20 → 4, суммы не совпадают со страницей города. Ключ `key` в запросе не
// обязателен — ответ с ним и без него побайтово одинаков.
//
// Горизонт у источника ровно два дня: страницы `today` и `tomorrow` отвечают,
// произвольная дата в пути — пустым списком, а `soon` и `all` не существуют.
// Это свойство источника, а не адаптера, и врать о нём нельзя: за глубиной
// обращаться некуда.
type cinema5Response struct {
	Items []struct {
		ID    int64  `json:"id"`
		Name  string `json:"name"`
		Shows []struct {
			ID       int64  `json:"id"`
			CinemaID int    `json:"cinemaId"`
			Date     string `json:"date"`
			Time     string `json:"time"`
			Format   string `json:"formatName"`
			Hall     string `json:"hallName"`
			HallCat  string `json:"hallCategory"`
			MinPrice int    `json:"minPrice"`
			MaxPrice int    `json:"maxPrice"`
		} `json:"shows"`
	} `json:"items"`
}

// parseCinema5 разбирает ответ афиши Синема 5.
//
// cinemaID здесь не фильтр, а ПРОВЕРКА: отбор уже сделал сервис, и чужой сеанс
// в ответе означал бы, что запрос ушёл не туда. Молча выбросить его нельзя —
// тогда промах по идентификатору выглядел бы как «сеансов мало».
func parseCinema5(body string, cinemaID int) (Playbill, error) {
	var resp cinema5Response
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return Playbill{}, fmt.Errorf("разбор ответа Синема 5: %w", err)
	}

	pb := Playbill{}
	dates := map[string]bool{}

	for _, m := range resp.Items {
		film := strings.TrimSpace(m.Name)
		if film == "" {
			continue
		}
		for _, s := range m.Shows {
			if cinemaID != 0 && s.CinemaID != cinemaID {
				return Playbill{}, fmt.Errorf(
					"разбор Синема 5: в ответе площадки %d пришёл сеанс площадки %d",
					cinemaID, s.CinemaID)
			}
			at := normalizeShowtime(s.Time, s.Date)
			if at == "" {
				continue
			}
			dates[s.Date] = true

			// Класс зала («Комфорт») едет в Format вместе с технологией: Hall
			// держит только номер, иначе два сеанса одного фильма в разных
			// залах одного формата схлопнулись бы в один ключ.
			format := strings.TrimSpace(s.Format)
			if cat := strings.TrimSpace(s.HallCat); cat != "" {
				format = strings.TrimSpace(format + " " + cat)
			}

			pb.Showtimes = append(pb.Showtimes, Showtime{
				Film:     film,
				StartsAt: at,
				Hall:     strings.TrimSpace(s.Hall),
				Format:   format,
				PriceMin: s.MinPrice,
				PriceMax: s.MaxPrice,
				SourceID: fmt.Sprintf("%d", s.ID),
				OnSale:   true,
			})
		}
	}

	for d := range dates {
		pb.Dates = append(pb.Dates, d)
	}
	sort.Strings(pb.Dates)

	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Синема 5: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// ——— PRIME CINEMA (движок etobilet) ———
//
// `primecinema.ru/?date=ДД.ММ.ГГГГ` отдаёт страницу, внутри которой лежит
// готовый JSON расписания дня — массив `daySchedule` в потоковых данных Next.js.
// Кавычки в нём экранированы (`\"`), потому что массив сам является строкой
// внутри разметки.
//
// Разбираем именно JSON, а не разметку: вёрстка у Next.js собрана из хешированных
// классов и меняется с каждой пересборкой, а форма данных живёт дольше.
//
// Дата, у которой расписание ещё не составлено, отдаёт пустой массив (в списке
// дат такой день помечен `isFormed: false`). Это не поломка канала: сеансов на
// эту дату у площадки пока нет.

type etobiletFilm struct {
	Name     string `json:"name"`
	Duration int    `json:"duration"`
	Formats  []struct {
		FormatName string `json:"format_name"`
		Halls      []struct {
			HallID   int `json:"hall_id"`
			Sessions []struct {
				SessionID int64  `json:"session_id"`
				Time      string `json:"time"`
				Price     string `json:"price"`
				Allow     int    `json:"allow"`
			} `json:"sessions"`
		} `json:"halls"`
	} `json:"formats"`
}

// etobiletPrice вытаскивает число из «650 руб.».
var etobiletPrice = regexp.MustCompile(`(\d+)`)

// parseEtobilet разбирает страницу площадки на движке etobilet.
func parseEtobilet(body, date string) (Playbill, error) {
	pb := Playbill{}
	if date != "" {
		pb.Dates = []string{date}
	}

	raw, err := extractEmbeddedJSON(body, `daySchedule`)
	if err != nil {
		return pb, fmt.Errorf("разбор PRIME CINEMA: %w", err)
	}

	var films []etobiletFilm
	if err := json.Unmarshal([]byte(raw), &films); err != nil {
		return pb, fmt.Errorf("разбор PRIME CINEMA: расписание дня не читается как JSON: %w", err)
	}

	for _, f := range films {
		film := strings.TrimSpace(html.UnescapeString(f.Name))
		if film == "" {
			continue
		}
		for _, fm := range f.Formats {
			for _, hl := range fm.Halls {
				for _, s := range hl.Sessions {
					at := normalizeShowtime(s.Time, date)
					if at == "" {
						continue
					}
					st := Showtime{
						Film:      film,
						StartsAt:  at,
						Hall:      strconv.Itoa(hl.HallID),
						Format:    strings.TrimSpace(fm.FormatName),
						SourceID:  fmt.Sprintf("%d", s.SessionID),
						OnSale:    s.Allow == 1,
						DurationM: f.Duration,
					}
					if p := etobiletPrice.FindString(s.Price); p != "" {
						st.PriceMin, _ = strconv.Atoi(p)
					}
					pb.Showtimes = append(pb.Showtimes, st)
				}
			}
		}
	}
	return pb, nil
}

// unescapeJSString снимает ОДИН слой экранирования, проходом слева направо.
//
// Двумя независимыми заменами это не делается, и на живых данных разница
// фатальна. Название «Одиссея \\"Авиарежим\\"» несёт кавычку ВНУТРИ строки, то
// есть экранированную дважды. Замена `\"`→`"` по всему телу превращает её в
// `\\"` — экранированный слеш плюс настоящая кавычка, и JSON обрывается прямо
// посреди названия. Проход же видит пару `\\` первой и отдаёт один слеш,
// оставляя внутреннюю кавычку экранированной, как ей и положено.
func unescapeJSString(s string) string {
	var b strings.Builder
	b.Grow(len(s))
	for i := 0; i < len(s); i++ {
		if s[i] != '\\' || i+1 >= len(s) {
			b.WriteByte(s[i])
			continue
		}
		switch s[i+1] {
		case '"':
			b.WriteByte('"')
			i++
		case '\\':
			b.WriteByte('\\')
			i++
		default:
			b.WriteByte(s[i])
		}
	}
	return b.String()
}

// splitBlocks режет разметку по повторяющемуся маркеру и отдаёт куски ПОСЛЕ
// каждого его вхождения.
//
// Заведено вместо нежадного регулярного выражения вида
// `маркер(.*?)(?:маркер|\z)`, и это не стилистика. Такой поиск теряет каждый
// второй блок: конец найденного куска включает следующий маркер, и обход
// продолжается уже за ним. На живой странице Пионера из семи фильмов так
// находились четыре, у Поклонки из пяти дней — четыре. Потеря молчаливая:
// афиша остаётся непустой, канал выглядит рабочим, а часть сеансов просто не
// доезжает.
//
// Та же ошибка уже ловилась на Премьерзале и была починена там локально —
// поэтому теперь приём общий, чтобы следующий адаптер не повторил её снова.
func splitBlocks(body, marker string) []string {
	parts := strings.Split(body, marker)
	if len(parts) < 2 {
		return nil
	}
	return parts[1:]
}

// extractEmbeddedJSON достаёт массив, лежащий строкой внутри разметки.
//
// Скобки считаются с учётом строк и экранирования — иначе закрывающая скобка из
// названия фильма («Одиссея (2D)») оборвала бы массив на середине, и потеря
// выглядела бы как «сеансов мало». Регулярным выражением такое не берётся в
// принципе: вложенность у него не считается.
//
// Ключ ищется в экранированном виде тоже: в потоковых данных Next.js кавычки
// внутри строки записаны как \", и обычный поиск по `"ключ":` их не находит.
func extractEmbeddedJSON(body, key string) (string, error) {
	unescaped := unescapeJSString(body)

	i := strings.Index(unescaped, `"`+key+`":`)
	if i < 0 {
		return "", fmt.Errorf("ключ %q не найден (тело %d байт)", key, len(body))
	}
	i += len(key) + 3

	for i < len(unescaped) && (unescaped[i] == ' ' || unescaped[i] == '\n') {
		i++
	}
	if i >= len(unescaped) || (unescaped[i] != '[' && unescaped[i] != '{') {
		return "", fmt.Errorf("значение ключа %q не массив и не объект", key)
	}

	open := unescaped[i]
	close := byte(']')
	if open == '{' {
		close = '}'
	}

	depth, inStr, esc := 0, false, false
	for j := i; j < len(unescaped); j++ {
		c := unescaped[j]
		switch {
		case esc:
			esc = false
		case c == '\\':
			esc = true
		case inStr:
			if c == '"' {
				inStr = false
			}
		case c == '"':
			inStr = true
		case c == open:
			depth++
		case c == close:
			depth--
			if depth == 0 {
				return unescaped[i : j+1], nil
			}
		}
	}
	return "", fmt.Errorf("значение ключа %q не закрыто", key)
}

// ——— Пионер ———
//
// `pioner-cinema.ru/?date=ГГГГ-ММ-ДД` отдаёт расписание дня серверным HTML.
// Горизонт — 8 дат, видных в переключателе (`data-date`); своего API у сайта
// нет, но разметка смысловая и переживает пересборку темы лучше, чем классы.
//
// Номера зала источник не даёт вовсе — у площадки один зал. Hall остаётся
// пустым: подставленное туда значение участвовало бы в ключе сеанса.
var (
	pionerTitle = regexp.MustCompile(`(?s)class="movie__title">\s*(.*?)\s*</a>`)
	pionerShow  = regexp.MustCompile(`data-seance-id="(\d+)"\s*>\s*([0-2]?\d:[0-5]\d)`)
	pionerDates = regexp.MustCompile(`data-date="(\d{4}-\d{2}-\d{2})"`)
)

func parsePioner(body, date string) (Playbill, error) {
	pb := Playbill{}
	for _, d := range pionerDates.FindAllStringSubmatch(body, -1) {
		pb.Dates = append(pb.Dates, d[1])
	}
	sort.Strings(pb.Dates)

	movies := splitBlocks(body, `sessions-items__movie movie">`)
	if len(movies) == 0 {
		return pb, fmt.Errorf("разбор Пионера: блоки фильмов не найдены (тело %d байт)", len(body))
	}

	for _, block := range movies {
		tm := pionerTitle.FindStringSubmatch(block)
		if len(tm) < 2 {
			continue
		}
		film := strings.TrimSpace(html.UnescapeString(stripHTML(tm[1])))
		if film == "" {
			continue
		}
		for _, s := range pionerShow.FindAllStringSubmatch(block, -1) {
			at := normalizeShowtime(s[2], date)
			if at == "" {
				continue
			}
			pb.Showtimes = append(pb.Showtimes, Showtime{
				Film: film, StartsAt: at, SourceID: s[1], OnSale: true,
			})
		}
	}
	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Пионера: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// ——— Поклонка (кинотеатр Музея Победы) ———
//
// `poklonka-cinema.ru/films/` отдаёт ВЕСЬ горизонт одним ответом: дни —
// блоки `seance-elem id="sN"`, а какой день какому N соответствует, говорит
// переключатель дат (`data-id="sN"` рядом с числом и месяцем).
//
// Внутри дня сеансы сгруппированы по залам, названным фамилиями маршалов
// («Зал Василевский»). Заголовок зала — не отдельный контейнер, а такой же
// `item`, поэтому сеансы разбираются потоком: заголовок переключает текущий
// зал, ссылки после него принадлежат ему.
var (
	poklonkaDayID   = regexp.MustCompile(`^(s\d+)">`)
	poklonkaDayTab  = regexp.MustCompile(`data-id="(s\d+)">(\d{1,2})<br>\s*([а-яё]+)`)
	poklonkaHallOrS = regexp.MustCompile(`(?s)<div class="item title">\s*(.*?)\s*</div>|<a class="item"[^>]*>\s*<div class="name">\s*(.*?)\s*</div>\s*<div class="value">\s*([0-2]?\d:[0-5]\d)\s*</div>`)
)

func parsePoklonka(body string, now time.Time) (Playbill, error) {
	pb := Playbill{}

	// Даты у источника без года: «6 августа». Год берём от текущего момента и
	// перекидываем вперёд при переходе через декабрь — иначе январские сеансы
	// уехали бы в прошлое и выглядели протухшим расписанием.
	dayDate := map[string]string{}
	for _, t := range poklonkaDayTab.FindAllStringSubmatch(body, -1) {
		d, err := strconv.Atoi(t[2])
		mon := russianMonth(t[3])
		if err != nil || mon == 0 {
			continue
		}
		year := now.Year()
		if mon < int(now.Month()) {
			year++
		}
		dayDate[t[1]] = fmt.Sprintf("%04d-%02d-%02d", year, mon, d)
	}

	days := splitBlocks(body, `<div class="seance-elem" id="`)
	if len(days) == 0 {
		return pb, fmt.Errorf("разбор Поклонки: блоки дней не найдены (тело %d байт)", len(body))
	}

	for _, day := range days {
		im := poklonkaDayID.FindStringSubmatch(day)
		if len(im) < 2 {
			continue
		}
		date := dayDate[im[1]]
		if date == "" {
			// День без даты в переключателе разбирать нельзя: время без даты
			// сеанса не образует.
			continue
		}
		pb.Dates = append(pb.Dates, date)

		hall := ""
		for _, m := range poklonkaHallOrS.FindAllStringSubmatch(day, -1) {
			if m[1] != "" {
				hall = strings.TrimSpace(html.UnescapeString(stripHTML(m[1])))
				continue
			}
			film := strings.TrimSpace(html.UnescapeString(stripHTML(m[2])))
			at := normalizeShowtime(m[3], date)
			if film == "" || at == "" {
				continue
			}
			pb.Showtimes = append(pb.Showtimes, Showtime{
				Film: film, StartsAt: at, Hall: hall, OnSale: true,
			})
		}
	}

	sort.Strings(pb.Dates)
	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Поклонки: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// russianMonth — номер месяца по русскому названию в родительном падеже.
// Ноль означает, что месяц не опознан: гадать нельзя, дата сеанса от него.
func russianMonth(s string) int {
	months := map[string]int{
		"января": 1, "февраля": 2, "марта": 3, "апреля": 4,
		"мая": 5, "июня": 6, "июля": 7, "августа": 8,
		"сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
	}
	return months[strings.ToLower(strings.TrimSpace(s))]
}

// ——— Кинотеатр «Москва» ———
//
// `cinema.moscow/repertoire` отдаёт репертуар серверным HTML. Формат площадка
// называет словами про удобство («зал с креслами»), а номера зала не даёт —
// поэтому оно едет в Format, а Hall остаётся пустым.
var (
	moskvaName   = regexp.MustCompile(`^\s*(.*?)\s*</div>`)
	moskvaFormat = regexp.MustCompile(`repertoire-times__format">\s*(.*?)\s*</div>`)
	moskvaDur    = regexp.MustCompile(`repertoire-times__dur">\s*(.*?)\s*</div>`)
	moskvaShow   = regexp.MustCompile(`data-href="/sessions/(\d+)"[^>]*>\s*([0-2]?\d:[0-5]\d)`)
)

func parseCinemaMoskva(body, date string) (Playbill, error) {
	pb := Playbill{}
	if date != "" {
		pb.Dates = []string{date}
	}

	blocks := splitBlocks(body, `repertoire-times__title">`)
	if len(blocks) == 0 {
		return pb, fmt.Errorf("разбор кинотеатра «Москва»: блоки фильмов не найдены (тело %d байт)", len(body))
	}

	for _, block := range blocks {
		nm := moskvaName.FindStringSubmatch(block)
		if len(nm) < 2 {
			continue
		}
		film := strings.TrimSpace(html.UnescapeString(stripHTML(nm[1])))
		if film == "" {
			continue
		}

		format := ""
		if fm := moskvaFormat.FindStringSubmatch(block); len(fm) > 1 {
			format = strings.TrimSpace(stripHTML(fm[1]))
		}
		dur := 0
		if dm := moskvaDur.FindStringSubmatch(block); len(dm) > 1 {
			dur = parseRussianDuration(dm[1])
		}

		for _, s := range moskvaShow.FindAllStringSubmatch(block, -1) {
			at := normalizeShowtime(s[2], date)
			if at == "" {
				continue
			}
			pb.Showtimes = append(pb.Showtimes, Showtime{
				Film: film, StartsAt: at, Format: format,
				SourceID: s[1], OnSale: true, DurationM: dur,
			})
		}
	}
	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор кинотеатра «Москва»: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// ——— Романов Синема ———
//
// Серверный HTML у площадки — ПУСТОЙ ШАБЛОН: название фильма «test», времена
// сеансов «00:00», залы «Зал 1»–«Зал 3». Разбор такой разметки дал бы непустую
// афишу из мусора, то есть площадка выглядела бы рабочей и отдавала выдуманные
// сеансы. Настоящее расписание отдаёт POST-ручка, найденная в трафике страницы.
//
// Ключ доступа публичный — лежит в скрипте сайта. Как и токены Kinoplan, он
// может смениться; тогда ручка начнёт отвечать 403, и это будет видно как
// поломка канала, а не как отсутствие сеансов.
const (
	romanovAPI    = "https://g84siu34vb.execute-api.eu-central-1.amazonaws.com/PHONE_API/seans"
	romanovAPIKey = "iMtuQpib7jaHUIoKdTtbv7H0nZIn6UAI3byWs9RP"
)

type romanovResponse struct {
	Halls map[string][]struct {
		ID     int64  `json:"SEANSES_ID"`
		Time   string `json:"SEANSES_TIME_FORMAT"`
		Film   string `json:"FILM_NAME"`
		Prices []struct {
			Price int `json:"PRICE"`
		} `json:"Prices"`
	} `json:"HALLS"`
}

// businessDayShift переносит ночной сеанс на следующий календарный день.
//
// Касса относит сеансы после полуночи к ПРЕДЫДУЩЕМУ операционному дню: у
// Романова в расписании на 5 августа стоят и 23:40, и 00:00, и второй идёт в
// ночь на 6-е. Без переноса такой сеанс приезжал бы на сутки раньше — человек
// пришёл бы не в тот день, а проверка «последний сеанс уже в прошлом» считала
// бы живое расписание протухшим.
//
// Граница в шесть утра — то же соглашение, что у операционного дня кинотеатра:
// раньше шести сеансов не бывает, а всё, что позже, принадлежит своему дню.
func businessDayShift(date, hhmm string) string {
	if date == "" || len(hhmm) < 2 {
		return date
	}
	h, err := strconv.Atoi(strings.TrimSpace(hhmm)[:2])
	if err != nil || h >= 6 {
		return date
	}
	d, err := time.ParseInLocation("2006-01-02", date, moscowTZ)
	if err != nil {
		return date
	}
	return d.AddDate(0, 0, 1).Format("2006-01-02")
}

// parseRomanov разбирает ответ ручки расписания.
//
// Зал приходит ключом карты («HALL1»), а не полем сеанса, поэтому номер
// вынимается из ключа: в Hall едет только он, без слова «HALL».
func parseRomanov(body, date string) (Playbill, error) {
	var resp romanovResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return Playbill{}, fmt.Errorf("разбор Романов Синема: %w", err)
	}

	pb := Playbill{}
	if date != "" {
		pb.Dates = []string{date}
	}

	// Порядок ключей карты в Go случаен, а сеансы должны ложиться устойчиво:
	// иначе два одинаковых прогона дадут разный порядок афиши.
	halls := make([]string, 0, len(resp.Halls))
	for h := range resp.Halls {
		halls = append(halls, h)
	}
	sort.Strings(halls)

	for _, h := range halls {
		hall := strings.TrimPrefix(h, "HALL")
		for _, s := range resp.Halls[h] {
			film := strings.TrimSpace(s.Film)
			at := normalizeShowtime(s.Time, businessDayShift(date, s.Time))
			if film == "" || at == "" {
				continue
			}
			st := Showtime{
				Film:     film,
				StartsAt: at,
				Hall:     hall,
				SourceID: fmt.Sprintf("%d", s.ID),
				OnSale:   true,
			}
			// Цен у сеанса несколько (по типам билета) — берём вилку, а не
			// первую попавшуюся. Цена приходит в копейках.
			for i, p := range s.Prices {
				rub := p.Price / 100
				if i == 0 || rub < st.PriceMin {
					st.PriceMin = rub
				}
				if rub > st.PriceMax {
					st.PriceMax = rub
				}
			}
			pb.Showtimes = append(pb.Showtimes, st)
		}
	}

	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Романов Синема: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// ——— Алмаз Синема ———
//
// `almazcinema.com/msk/cinema/<id>/schedule/` отдаёт страницу, где каждый
// сеанс лежит готовым JSON в атрибуте кнопки покупки. Разбираем его, а не
// текст рядом: в атрибуте есть и зона времени, и вилка цены, и зал, а в
// подписи — только час.
//
// Доступен только через российский выход.
type almazSession struct {
	DateTimeOffset string `json:"DateTimeOffset"`
	Format         string `json:"Format"`
	MinPrice       string `json:"MinPrice"`
	MaxPrice       string `json:"MaxPrice"`
	HallName       string `json:"HallName"`
	SaleAvailable  bool   `json:"IsSaleAvailable"`
	// SessionID — идентификатор СЕАНСА. CreationObjectID рядом в том же
	// объекте — идентификатор фильма, и он одинаков у всех его сеансов:
	// взятый в SourceID, он схлопнул бы их в один.
	ID int64 `json:"SessionID"`
}

var (
	almazBtn      = regexp.MustCompile(`data-data='([^']+)'`)
	almazFilmName = regexp.MustCompile(`(?s)^\s*(.*?)\s*</h3>`)
	almazHallOnly = regexp.MustCompile(`(\d+)`)
)

func parseAlmaz(body, date string) (Playbill, error) {
	pb := Playbill{}
	if date != "" {
		pb.Dates = []string{date}
	}

	// Фильм и его сеансы идут одним блоком: название лежит в <h3>, сеансы —
	// кнопками после него. Режем по заголовку, внутри куска собираем кнопки.
	blocks := splitBlocks(body, `<h3>`)
	if len(blocks) == 0 {
		return pb, fmt.Errorf("разбор Алмаза: блоки фильмов не найдены (тело %d байт)", len(body))
	}

	for _, block := range blocks {
		fm := almazFilmName.FindStringSubmatch(block)
		if len(fm) < 2 {
			continue
		}
		film := strings.TrimSpace(html.UnescapeString(stripHTML(fm[1])))
		if film == "" {
			continue
		}

		for _, bm := range almazBtn.FindAllStringSubmatch(block, -1) {
			var s almazSession
			if err := json.Unmarshal([]byte(html.UnescapeString(bm[1])), &s); err != nil {
				continue
			}
			at := parseZonedTime(s.DateTimeOffset)
			if at == "" {
				continue
			}

			st := Showtime{
				Film:     film,
				StartsAt: at,
				Format:   strings.TrimSpace(s.Format),
				SourceID: fmt.Sprintf("%d", s.ID),
				OnSale:   s.SaleAvailable,
			}
			// «Зал №1» — в Hall едет только номер: слово «Зал» одинаково у всех
			// и в ключе сеанса ничего не различает.
			if hm := almazHallOnly.FindStringSubmatch(s.HallName); len(hm) > 1 {
				st.Hall = hm[1]
			}
			st.PriceMin, _ = strconv.Atoi(strings.TrimSpace(s.MinPrice))
			st.PriceMax, _ = strconv.Atoi(strings.TrimSpace(s.MaxPrice))
			pb.Showtimes = append(pb.Showtimes, st)
		}
	}

	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Алмаза: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// ——— Иллюзион ———
//
// `illusion-cinema.ru/schedule/` отдаёт ВЕСЬ горизонт одним ответом: дни идут
// заголовками «4 августа, вторник», внутри дня — сеансы. Параметра даты у
// источника нет вовсе, и это не ограничение адаптера, а устройство страницы.
//
// Название несёт зал: «МАЛЫЙ ЗАЛ. Питер ФМ». Зал отделяется, потому что иначе
// один и тот же фильм в разных залах выглядел бы разными фильмами и не совпал
// бы с искомым названием.
//
// Доступен только через российский выход.
var (
	illusionDayHdr = regexp.MustCompile(`^\s*(\d{1,2})\s+([а-яё]+)`)
	illusionTime   = regexp.MustCompile(`schedule-film__time">\s*([0-2]?\d:[0-5]\d)`)
	illusionName   = regexp.MustCompile(`(?s)schedule-film__name">\s*(.*?)\s*</span>`)
	illusionHall   = regexp.MustCompile(`^\s*([А-ЯЁ][А-ЯЁ\s]{2,20}ЗАЛ)\.\s*(.+)$`)
)

func parseIllusion(body string, now time.Time) (Playbill, error) {
	pb := Playbill{}

	days := splitBlocks(body, `<h2>`)
	if len(days) == 0 {
		return pb, fmt.Errorf("разбор Иллюзиона: дни не найдены (тело %d байт)", len(body))
	}

	for _, day := range days {
		hm := illusionDayHdr.FindStringSubmatch(day)
		if len(hm) < 3 {
			continue
		}
		d, err := strconv.Atoi(hm[1])
		mon := russianMonth(hm[2])
		if err != nil || mon == 0 {
			continue
		}
		year := now.Year()
		if mon < int(now.Month()) {
			year++
		}
		date := fmt.Sprintf("%04d-%02d-%02d", year, mon, d)
		pb.Dates = append(pb.Dates, date)

		for _, item := range splitBlocks(day, `schedule-film__time">`) {
			tm := illusionTime.FindStringSubmatch(`schedule-film__time">` + item)
			nm := illusionName.FindStringSubmatch(item)
			if len(tm) < 2 || len(nm) < 2 {
				continue
			}
			film := strings.TrimSpace(html.UnescapeString(stripHTML(nm[1])))
			hall := ""
			if p := illusionHall.FindStringSubmatch(film); len(p) > 2 {
				hall, film = strings.TrimSpace(p[1]), strings.TrimSpace(p[2])
			}
			at := normalizeShowtime(tm[1], businessDayShift(date, tm[1]))
			if film == "" || at == "" {
				continue
			}
			pb.Showtimes = append(pb.Showtimes, Showtime{
				Film: film, StartsAt: at, Hall: hall, OnSale: true,
			})
		}
	}

	sort.Strings(pb.Dates)
	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Иллюзиона: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// ——— Люксор ———
//
// `luxorfilm.ru/cinema/<slug>/seances` отдаёт страницу, внутри которой лежит
// готовый массив `filmsAll` — фильмы вместе со своими сеансами. Разбираем его,
// а не разметку: в массиве есть зал, технология и вилка цены.
//
// Доступен только через российский выход: напрямую сайт отвечает 403 от
// DDoS-Guard. Прежняя разведка объявила его недоступным вовсе — вердикт был
// снят повторной проверкой через туннель, там он берётся обычным запросом.
type luxorFilm struct {
	Title   string `json:"title"`
	Seances []struct {
		ID        int64  `json:"session_id"`
		Time      string `json:"time"`
		Tech      string `json:"tech"`
		Hall      string `json:"hall"`
		MinPrice  int    `json:"minprice"`
		MaxPrice  int    `json:"maxprice"`
		SeanceKey int64  `json:"id"`
	} `json:"seances"`
}

var luxorHallNum = regexp.MustCompile(`(\d+)`)

func parseLuxor(body, date string) (Playbill, error) {
	pb := Playbill{}
	if date != "" {
		pb.Dates = []string{date}
	}

	raw, err := extractEmbeddedJSON(body, "filmsAll")
	if err != nil {
		// Ключ ищется и как `"filmsAll":`, и как присваивание в скрипте —
		// у этого источника он второй формы.
		i := strings.Index(body, "filmsAll = ")
		if i < 0 {
			return pb, fmt.Errorf("разбор Люксора: массив фильмов не найден (тело %d байт)", len(body))
		}
		raw, err = extractJSONAt(body[i+len("filmsAll = "):])
		if err != nil {
			return pb, fmt.Errorf("разбор Люксора: %w", err)
		}
	}

	var films []luxorFilm
	if err := json.Unmarshal([]byte(raw), &films); err != nil {
		return pb, fmt.Errorf("разбор Люксора: массив фильмов не читается как JSON: %w", err)
	}

	// Пустой день у Люксора выглядит как `filmsAll = []`: массив на месте и
	// читается, просто фильмов в нём нет. Это ответ источника — сеансы дня
	// кончились или день ещё не открыт, — а не сменившаяся вёрстка, и путать
	// их нельзя: иначе живая площадка вечером объявляется мёртвой.
	//
	// Граница ровно здесь. Массив, который не нашёлся или не разобрался,
	// по-прежнему ошибка; непустой массив без единого сеанса — тоже (значит
	// сменились поля внутри, и молчать об этом опаснее всего).
	if len(films) == 0 {
		return pb, nil
	}

	for _, f := range films {
		film := strings.TrimSpace(f.Title)
		if film == "" {
			continue
		}
		for _, s := range f.Seances {
			at := normalizeShowtime(s.Time, businessDayShift(date, s.Time))
			if at == "" {
				continue
			}
			st := Showtime{
				Film:     film,
				StartsAt: at,
				Format:   strings.TrimSpace(s.Tech),
				PriceMin: s.MinPrice,
				PriceMax: s.MaxPrice,
				SourceID: fmt.Sprintf("%d", s.ID),
				OnSale:   true,
			}
			// «Зал 4» — в Hall едет только номер.
			if hm := luxorHallNum.FindStringSubmatch(s.Hall); len(hm) > 1 {
				st.Hall = hm[1]
			}
			pb.Showtimes = append(pb.Showtimes, st)
		}
	}

	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Люксора: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// extractJSONAt отрезает один JSON-массив или объект от начала строки.
//
// Нужен там, где значение не подписано ключом, а присвоено переменной в
// скрипте: искать его конец приходится счётом скобок — по тем же причинам, что
// и в extractEmbeddedJSON, только без поиска ключа.
func extractJSONAt(s string) (string, error) {
	i := 0
	for i < len(s) && (s[i] == ' ' || s[i] == '\n' || s[i] == '\t') {
		i++
	}
	if i >= len(s) || (s[i] != '[' && s[i] != '{') {
		return "", fmt.Errorf("значение не массив и не объект")
	}
	open := s[i]
	closing := byte(']')
	if open == '{' {
		closing = '}'
	}

	depth, inStr, esc := 0, false, false
	for j := i; j < len(s); j++ {
		c := s[j]
		switch {
		case esc:
			esc = false
		case c == '\\':
			esc = true
		case inStr:
			if c == '"' {
				inStr = false
			}
		case c == '"':
			inStr = true
		case c == open:
			depth++
		case c == closing:
			depth--
			if depth == 0 {
				return s[i : j+1], nil
			}
		}
	}
	return "", fmt.Errorf("значение не закрыто")
}

// ——— Третьяковская галерея ———
//
// `tretyakovgallery.ru/tickets/cinema/` отдаёт страницу, где сеансы лежат в
// потоковых данных Next.js: у каждого показа свой url `/cinema/o/<слаг>/` и
// массив `session_dates` вида «06.08.2026 19:00:00». Времён в самой разметке
// нет — по срезу тегов страница выглядит расписанием без сеансов.
//
// Зал при этом живёт как раз в РАЗМЕТКЕ: рядом со ссылкой показа стоит
// `<span>Инженерный корпус</span>`. Он обязателен: строк реестра у Третьяковки
// две (Инженерный корпус и Новая Третьяковка), и без отбора обе получили бы
// одну афишу — ровно та ловушка «афиша всех площадок разом», на которой уже
// попадались Kinoplan и Мираж.
//
// Доступна только через российский выход.
var (
	tretyakovHallLink = regexp.MustCompile(`<span[^>]*>([^<]{4,40})</span></a>\s*<a href="/cinema/o/([a-z0-9-]+)/"`)
	// Ключи в потоковых данных Next.js идут БЕЗ кавычек (минифицированный
	// объект). Классы символов вместо `.*?` намеренно: нежадный поиск через
	// весь документ перескакивал границы объекта и утаскивал в название
	// фильма половину страницы.
	tretyakovName  = regexp.MustCompile(`name:"([^"]{3,200})",picture:"[^"]*"$`)
	tretyakovDates = regexp.MustCompile(`session_dates:\[([^\]]*)\]`)
	tretyakovDate  = regexp.MustCompile(`"(\d{2})\.(\d{2})\.(\d{4}) (\d{2}:\d{2}):\d{2}"`)
)

// parseTretyakov разбирает афишу кинозалов галереи.
//
// hall — название корпуса; пустое означает «взять все», но в реестре так не
// используется: там у каждой строки свой корпус.
func parseTretyakov(body, hall string) (Playbill, error) {
	pb := Playbill{}

	// Зал → слаг показа берём из разметки, сеансы — из данных. Связывает их
	// слаг: он есть и там, и там.
	hallOf := map[string]string{}
	for _, m := range tretyakovHallLink.FindAllStringSubmatch(body, -1) {
		hallOf[m[2]] = strings.TrimSpace(html.UnescapeString(m[1]))
	}
	if len(hallOf) == 0 {
		return pb, fmt.Errorf("разбор Третьяковки: показы с залами не найдены (тело %d байт)", len(body))
	}

	// Слеши в потоковых данных экранированы как \u002F — без обратной замены
	// путь показа в них не найти.
	unescaped := strings.ReplaceAll(body, `\u002F`, "/")
	dates := map[string]bool{}
	seen := false

	// Режем по адресу показа: слаг открывает кусок, даты лежат внутри него, а
	// название — в хвосте ПРЕДЫДУЩЕГО куска, прямо перед адресом.
	const urlMark = `,url:"/cinema/o/`
	chunks := strings.Split(unescaped, urlMark)

	for i := 1; i < len(chunks); i++ {
		slug, rest, ok := strings.Cut(chunks[i], `/"`)
		if !ok {
			continue
		}
		venue := hallOf[slug]
		if venue == "" {
			continue
		}
		nm := tretyakovName.FindStringSubmatch(chunks[i-1])
		if len(nm) < 2 {
			continue
		}
		film := strings.TrimSpace(html.UnescapeString(nm[1]))
		if film == "" {
			continue
		}
		if hall != "" && venue != hall {
			continue
		}
		seen = true

		dm := tretyakovDates.FindStringSubmatch(rest)
		if len(dm) < 2 {
			continue
		}
		for _, d := range tretyakovDate.FindAllStringSubmatch(dm[1], -1) {
			date := d[3] + "-" + d[2] + "-" + d[1]
			at := normalizeShowtime(d[4], businessDayShift(date, d[4]))
			if at == "" {
				continue
			}
			dates[date] = true
			pb.Showtimes = append(pb.Showtimes, Showtime{
				Film: film, StartsAt: at, Hall: venue, OnSale: true,
			})
		}
	}

	for d := range dates {
		pb.Dates = append(pb.Dates, d)
	}
	sort.Strings(pb.Dates)

	// Корпуса нет на странице — это НЕ пустая афиша, а промах по названию зала:
	// молча отдать пустоту значило бы выдать «сеансов нет» за факт.
	if hall != "" && !seen {
		return pb, fmt.Errorf("разбор Третьяковки: показов в зале %q на странице нет", hall)
	}
	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Третьяковки: сеансы не найдены (тело %d байт)", len(body))
	}
	return pb, nil
}

// ——— Еврейский музей и центр толерантности ———
//
// `jewish-museum.ru/events/` отдаёт афишу карточками: название со ссылкой на
// событие, дата и время одной строкой («09.08.2026, 20:15»), цена.
//
// В афише вперемешку лекции, экскурсии и кинопоказы, поэтому нужен ОТБОР. Он
// сделан по признаку показа в названии и адресе события: площадка называет
// кинопоказы прямо. Без отбора в афишу поехали бы лекции, и поиск фильма стал
// бы находить их по совпадению слов.
//
// Проверено 04.08.2026: в афише стоит «Непокой» — текущий прокат, тот же, что
// в мультиплексах. Поэтому площадка и не попала в класс «не показывает прокат».
var (
	jewishName  = regexp.MustCompile(`(?s)small-card__name[^"]*"\s*href="(/events/[a-z0-9-]+/)">\s*(.*?)\s*</a>`)
	jewishWhen  = regexp.MustCompile(`(\d{2})\.(\d{2})\.(\d{4}),\s*(\d{2}:\d{2})`)
	jewishPrice = regexp.MustCompile(`event-card__price">[^\d]*(\d+)`)
)

// screeningWords — по чему опознаётся кинопоказ среди прочих событий.
//
// Список короткий и явный намеренно: отбор по нему решает, что попадёт в афишу,
// и молчаливая эвристика здесь была бы опаснее пропуска. Слово ищется и в
// названии, и в адресе события — площадки пишут его то там, то там.
var screeningWords = []string{"кинопоказ", "киносеанс", "kinopokaz", "kinoseans", "показ фильма"}

func looksLikeScreening(title, url string) bool {
	low := strings.ToLower(title + " " + url)
	for _, w := range screeningWords {
		if strings.Contains(low, w) {
			return true
		}
	}
	return false
}

func parseJewishMuseum(body string) (Playbill, error) {
	pb := Playbill{}

	// Карточка события — самостоятельный блок; название и дата лежат внутри
	// него, а первая ссылка карточки ведёт на картинку, не на название.
	cards := splitBlocks(body, `class="event-card small-card`)
	if len(cards) == 0 {
		return pb, fmt.Errorf("разбор Еврейского музея: карточки событий не найдены (тело %d байт)", len(body))
	}

	dates := map[string]bool{}
	for _, card := range cards {
		nm := jewishName.FindStringSubmatch(card)
		if len(nm) < 3 {
			continue
		}
		url := nm[1]
		title := strings.TrimSpace(html.UnescapeString(stripHTML(nm[2])))
		if title == "" || !looksLikeScreening(title, url) {
			continue
		}
		when := card
		wm := jewishWhen.FindStringSubmatch(when)
		if len(wm) < 5 {
			continue
		}
		date := wm[3] + "-" + wm[2] + "-" + wm[1]
		at := normalizeShowtime(wm[4], businessDayShift(date, wm[4]))
		if at == "" {
			continue
		}
		dates[date] = true

		st := Showtime{Film: title, StartsAt: at, OnSale: true, DeepLink: "https://www.jewish-museum.ru" + url}
		if pm := jewishPrice.FindStringSubmatch(when); len(pm) > 1 {
			st.PriceMin, _ = strconv.Atoi(pm[1])
		}
		pb.Showtimes = append(pb.Showtimes, st)
	}

	for d := range dates {
		pb.Dates = append(pb.Dates, d)
	}
	sort.Strings(pb.Dates)

	if len(pb.Showtimes) == 0 {
		return pb, fmt.Errorf("разбор Еврейского музея: кинопоказов в афише нет (событий %d)", len(cards))
	}
	return pb, nil
}
