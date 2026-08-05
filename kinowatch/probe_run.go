package main

// Опрос площадок по фильму: реестр на вход, статус каждой площадки на выход.
//
// Здесь соединяются три уже написанные части — сборка запроса к каналу
// (channel.go), каскад матчинга (film.go) и классификатор исхода (probe.go).
// Своей логики тут минимум, и это намеренно: решения о том, найден ли фильм и
// жив ли источник, принимаются чистыми функциями, которые целиком накрыты
// табличными тестами.
//
// Обход последовательный. Чужие кассы не наш сервер, и десяток параллельных
// запросов к одной сети ради минуты выигрыша — плохая сделка.

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"time"
)

// ProbeReport — то, что отдаёт --probe.
type ProbeReport struct {
	FetchedAt string      `json:"fetchedAt"`
	Film      FilmProfile `json:"film"`
	Days      int         `json:"days"`

	// Probed и Skipped в сумме дают весь реестр: площадка не может тихо
	// выпасть из отчёта, иначе непокрытость выглядела бы как её отсутствие.
	Probed   int            `json:"probed"`
	Skipped  int            `json:"skipped"`
	Statuses map[string]int `json:"statuses"`

	// Tunnel — сколько площадок требуют российского выхода и сколько из них
	// осталось неопрошенными. Отдельно от прочих причин намеренно: несобранное
	// из-за отсутствия туннеля — это «не дотянулись», а не «источник сломался».
	Tunnel TunnelStats `json:"tunnel"`

	Venues []VenueProbe `json:"venues"`

	// Observations — тот же реестр, что пришёл на вход, с записанным итогом
	// прогона по каждой площадке.
	//
	// Без него цепочка «канал ответил живьём → это видно в реестре» рвётся, и
	// покрытие снова становится тем, что агент себе проставил. Отдаётся целиком,
	// чтобы следующий прогон и счётчик покрытия читали ФАКТ ответа, а не пометку.
	Observations []CinemaObservation `json:"observations"`

	// Aggregator — второй слой: тот же фильм глазами Яндекс Афиши. Нужен и для
	// доп-покрытия (площадки без своего канала), и как контроль качества —
	// расхождение слоёв видно числами в Agreement.
	Aggregator *AggregatorLayer `json:"aggregator,omitempty"`
}

// VenueProbe — итог по одной площадке.
type VenueProbe struct {
	Key    string `json:"key"`
	Name   string `json:"name"`
	Kind   string `json:"kind,omitempty"`
	Venue  string `json:"venue,omitempty"`
	Status string `json:"status"`
	// Evidence отвечает на вопрос «почему такой статус». Без него чужой прогон
	// разбирается гаданием.
	Evidence string `json:"evidence,omitempty"`
	Alive    bool   `json:"alive"`

	// Found — сеансы искомого фильма. Пустой список при статусе absent — это
	// результат, а при статусе поломки — просто отсутствие данных.
	Found []FoundShowtime `json:"found,omitempty"`

	// FailedDays — даты, которых канал не отдал. Пустой горизонт делает вывод
	// «фильма нет» недоказуемым, см. ниже.
	FailedDays []string `json:"failedDays,omitempty"`

	// SkipReason непустая означает, что площадку не опрашивали вовсе.
	SkipReason string `json:"skipReason,omitempty"`

	// FromAggregator — сеансы этого же фильма у агрегатора. Лежат ОТДЕЛЬНО от
	// Found, а не сливаются с ними: как только поля перемешаются, сравнивать
	// слои станет не с чем и второй слой перестанет быть контролем качества.
	FromAggregator []AggregatorShowtime `json:"fromAggregator,omitempty"`
}

// FoundShowtime — найденный сеанс вместе с объяснением, чем он опознан.
type FoundShowtime struct {
	StartsAt   string `json:"startsAt"`
	Title      string `json:"title"`
	By         string `json:"by"`
	Confidence string `json:"confidence"`
	OnSale     bool   `json:"onSale"`
	Grey       bool   `json:"grey,omitempty"`
	Hall       string `json:"hall,omitempty"`
	PriceMin   int    `json:"priceMin,omitempty"`
}

// readRegistry читает наблюдения со stdin.
//
// Принимается и отчёт --enrich целиком, и голый список наблюдений: рутина
// хранит у себя первое, а руками удобнее скормить второе.
func readRegistry(r io.Reader) ([]CinemaObservation, error) {
	raw, err := io.ReadAll(r)
	if err != nil {
		return nil, fmt.Errorf("чтение реестра: %w", err)
	}
	if len(strings.TrimSpace(string(raw))) == 0 {
		return nil, fmt.Errorf("реестр пуст: ожидается отчёт --enrich или список наблюдений")
	}

	var report struct {
		Observations []CinemaObservation `json:"observations"`
	}
	if err := json.Unmarshal(raw, &report); err == nil && len(report.Observations) > 0 {
		return report.Observations, nil
	}

	var list []CinemaObservation
	if err := json.Unmarshal(raw, &list); err != nil {
		return nil, fmt.Errorf("разбор реестра: %w", err)
	}
	if len(list) == 0 {
		return nil, fmt.Errorf("в реестре ноль площадок")
	}
	return list, nil
}

// loadFilmProfile собирает профиль искомого фильма.
//
// Одного названия хватает для проката без маскировки. Профиль файлом нужен
// там, где фильм идёт «предсеансовым обслуживанием»: тогда решают обёртки,
// хронометраж и синопсис, а названия в афише нет вовсе.
func loadFilmProfile(title, path string) (FilmProfile, error) {
	if path == "" {
		if strings.TrimSpace(title) == "" {
			return FilmProfile{}, fmt.Errorf("нужен --film или --film-profile")
		}
		return FilmProfile{Title: strings.TrimSpace(title)}, nil
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		return FilmProfile{}, fmt.Errorf("чтение профиля фильма: %w", err)
	}
	var p FilmProfile
	if err := json.Unmarshal(raw, &p); err != nil {
		return FilmProfile{}, fmt.Errorf("разбор профиля фильма: %w", err)
	}
	if strings.TrimSpace(title) != "" {
		p.Title = strings.TrimSpace(title)
	}
	if strings.TrimSpace(p.Title) == "" {
		return FilmProfile{}, fmt.Errorf("в профиле фильма нет названия")
	}
	return p, nil
}

// runProbe опрашивает реестр. tunnel — клиент через российский выход; nil
// означает, что туннеля нет и площадки, которым он нужен, опрошены не будут.
func runProbe(c, tunnel *Client, title, profilePath string, days int) {
	film, err := loadFilmProfile(title, profilePath)
	if err != nil {
		fail("%v", err)
	}
	obs, err := readRegistry(os.Stdin)
	if err != nil {
		fail("%v", err)
	}

	now := time.Now()
	report := ProbeReport{
		FetchedAt: nowRFC3339(),
		Film:      film,
		Days:      days,
		Statuses:  map[string]int{},
	}

	// Второй слой опрашивается ПЕРВЫМ и одним запросом: он отвечает за фильм
	// целиком, а не за площадку, и его отказ должен быть виден до того, как
	// прогон потратит час на обход своих касс.
	layer, sessions := runAggregatorLayer(c, film, obs, now, days)
	report.Aggregator = layer

	for i := range obs {
		// Площадке, недоступной с иностранного адреса, общий клиент не годится:
		// без туннеля она молча выглядела бы сломанной. Туннеля нет — площадка
		// пропускается с явной причиной, и это ЧЕСТНЕЕ, чем записать ей отказ
		// источника, которого не было.
		client := c
		if requiresTunnel(obs[i].Fields[fSourceKind]) {
			report.Tunnel.Required++
			if tunnel == nil {
				report.Tunnel.Skipped++
				vp := VenueProbe{
					Key: obs[i].Key, Name: obs[i].Name,
					Kind:       obs[i].Fields[fSourceKind],
					SkipReason: "каналу нужен российский выход, туннель не задан (--proxy)",
				}
				report.Skipped++
				report.Venues = append(report.Venues, vp)
				continue
			}
			client = tunnel
		}

		vp := probeVenue(client, obs[i], film, now, days)
		if vp.SkipReason != "" {
			report.Skipped++
		} else {
			report.Probed++
			report.Statuses[vp.Status]++
			recordProbe(&obs[i], vp, report.FetchedAt)
		}
		report.Venues = append(report.Venues, vp)
	}
	report.Observations = obs

	// Сеансы агрегатора раскладываются по строкам реестра уже после обхода:
	// привязка знает про строки, а не про то, что ответил их собственный канал.
	if layer != nil && layer.Err == "" {
		byRow := aggregatorShowtimesByRow(layer, sessions)
		ownFound := map[string]bool{}
		for i := range report.Venues {
			vp := &report.Venues[i]
			if vp.SkipReason == "" {
				ownFound[vp.Key] = len(vp.Found) > 0
			}
			vp.FromAggregator = byRow[vp.Key]
		}
		layer.Agreement = countAgreement(ownFound, byRow)
	}

	out, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		fail("сериализация: %v", err)
	}
	fmt.Println(string(out))

	// Ноль опрошенных площадок — отказ прогона, а не пустой результат: молча
	// напечатанный отчёт без единого опроса читается как «фильма нигде нет».
	if report.Probed == 0 {
		fail("не опрошено ни одной площадки из %d — проверь, что в реестре есть каналы", len(obs))
	}
}

// runAggregatorLayer опрашивает второй слой и решает, можно ли идти дальше.
//
// Отказы у него разной природы, и смешивать их нельзя.
//
// Прогон ОСТАНАВЛИВАЕТСЯ, когда опознание фильма провалилось по существу: по
// названию идёт несколько фильмов и выбирать за Влада нельзя, либо карточка
// противоречит профилю — значит спросили не про тот фильм, и весь результат
// прогона был бы не про него.
//
// Прогон ПРОДОЛЖАЕТСЯ, когда слоя просто нет: фильм ещё не вышел или уже сошёл
// с проката, агрегатор его не знает, сеть не дошла. Уронить из-за этого первый
// слой значило бы потерять собственные кассы там, где они как раз работают.
func runAggregatorLayer(c *Client, film FilmProfile, obs []CinemaObservation, now time.Time, days int) (*AggregatorLayer, []YandexSession) {
	layer, sessions, err := runYandexLayer(c, film, obs, now, days)
	if _, fatal := err.(aggregatorFatal); fatal {
		fail("%v", err)
	}
	return layer, sessions
}

// recordProbe кладёт итог опроса в саму строку реестра.
//
// LastOk — время последнего ДОКАЗАННО живого ответа, и обновляется только по
// доказанной живости. Именно оно, а не пометка класса, отвечает на вопрос «у
// этой площадки есть работающий инструмент»: живость доказывается ответом
// источника, назначить её себе нельзя.
//
// Прошлое значение LastOk при неудачном прогоне не стирается — иначе одна
// сетевая ошибка обнуляла бы всю историю площадки.
func recordProbe(o *CinemaObservation, vp VenueProbe, now string) {
	o.Fields[fLastStatus] = vp.Status
	o.Fields[fStatusAt] = now

	if vp.Alive {
		o.Fields[fLastOk] = now
		delete(o.Fields, fLastError)
		return
	}
	if vp.Evidence != "" {
		o.Fields[fLastError] = vp.Evidence
	}
}

// probeVenue опрашивает одну площадку.
func probeVenue(c *Client, o CinemaObservation, film FilmProfile, now time.Time, days int) VenueProbe {
	vp := VenueProbe{
		Key:   o.Key,
		Name:  o.Name,
		Kind:  o.Fields[fSourceKind],
		Venue: o.Fields[fSourceParams],
	}

	if vp.Kind == "" {
		vp.Venue = ""
		vp.SkipReason = skipReason(o)
		return vp
	}

	probe := fetchChannel(c, vp.Kind, parseChannelParams(o.Fields[fSourceParams]), now, days)
	vp.FailedDays = probe.FailedDays

	matches := matchPlaybill(probe.Playbill, film)
	found, onSale := false, false
	for i, m := range matches {
		if !m.Matched {
			continue
		}
		found = true
		s := probe.Playbill.Showtimes[i]
		if s.OnSale {
			onSale = true
		}
		vp.Found = append(vp.Found, FoundShowtime{
			StartsAt:   s.StartsAt,
			Title:      s.Film,
			By:         m.By,
			Confidence: m.Confidence,
			OnSale:     s.OnSale,
			Grey:       m.GreyRelease,
			Hall:       s.Hall,
			PriceMin:   s.PriceMin,
		})
	}

	res := classifyProbe(ProbeInput{
		Err:        probe.Err,
		HTTPStatus: probe.Status,
		BodySize:   probe.BodySize,
		ParseErr:   probe.ParseErr,
		Playbill:   probe.Playbill,
		FilmFound:  found,
		FilmOnSale: onSale,
		Now:        now,
	})

	res = applyHorizonGap(res, probe.FailedDays)
	vp.Status, vp.Evidence, vp.Alive = res.Status, res.Evidence, res.Alive
	return vp
}

// applyHorizonGap запрещает вывод «фильма нет» по неполному горизонту.
//
// Классификатор судит по одному ответу и про пропущенные дни не знает. А «нет»
// — утверждение обо ВСЁМ горизонте: если канал не отдал три дня из семи, фильм
// мог идти именно в них. Находка от этого не страдает — она положительна и уже
// сделана, — поэтому понижается только absent.
//
// Живость источника при этом остаётся доказанной: он ответил за остальные дни.
func applyHorizonGap(res ProbeResult, failedDays []string) ProbeResult {
	if res.Status != statusAbsent || len(failedDays) == 0 {
		return res
	}
	res.Status = statusSuspect
	res.Evidence = fmt.Sprintf("горизонт неполон, канал не ответил за %d дн. (%s): %s",
		len(failedDays), strings.Join(failedDays, ", "), res.Evidence)
	return res
}

// skipReason объясняет, почему площадку не опрашивали.
//
// Причина обязана быть словами, а не пустотой: непокрытая площадка и площадка
// без сеансов по своей природе — разные вещи, и в отчёте они не должны
// выглядеть одинаково.
func skipReason(o CinemaObservation) string {
	if e := strings.TrimSpace(o.Fields[fLastError]); e != "" {
		return e
	}
	switch o.Fields[fStatusClass] {
	case classCloneOf:
		return "клон другой записи реестра, сеансы пишет ведущая"
	case classNoOnlineSale:
		return "площадка без сущности «сеанс»"
	case "":
		return "канал не назначен"
	default:
		return "канала нет: " + o.Fields[fStatusClass]
	}
}
