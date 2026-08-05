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

	// Sales отвечает на вопрос, ради которого затеян дальний горизонт: где уже
	// открыта продажа. Без него открытие предпродажи тонет в общем списке
	// площадок.
	Sales SalesSummary `json:"sales"`

	// Diff — что изменилось против прошлого прогона. Заполняется, только когда
	// снимок прошлого прогона дан на вход.
	Diff *RunDiff `json:"diff,omitempty"`

	// Records — что достроено к поданному реестру записями из кода.
	//
	// Прогон берёт реестр со stdin, и до этого поштучные записи применялись
	// только при его пересборке. Канал, найденный руками и дописанный в код,
	// доезжал до опроса лишь после дорогого --enrich с геокодером: на снимке
	// сегодняшнего прогона так молчали 34 записи каналов из 48 и все 16
	// записей о закрытии.
	Records RecordsApplied `json:"records"`
}

// SalesSummary — сводка по продажам искомого фильма за прогон.
type SalesSummary struct {
	// EarliestDate — самый ранний сеанс по городу. Пустая строка означает, что
	// фильм не найден нигде.
	EarliestDate string `json:"earliestDate,omitempty"`
	// Venues — сколько площадок уже продают, Showtimes — сколько всего сеансов.
	Venues    int `json:"venues"`
	Showtimes int `json:"showtimes"`
}

// buildSalesSummary собирает сводку продаж по опрошенным площадкам.
func buildSalesSummary(venues []VenueProbe) SalesSummary {
	var out SalesSummary
	for _, vp := range venues {
		if len(vp.Found) == 0 {
			continue
		}
		out.Venues++
		out.Showtimes += len(vp.Found)
		if out.EarliestDate == "" || (vp.SaleFrom != "" && vp.SaleFrom < out.EarliestDate) {
			out.EarliestDate = vp.SaleFrom
		}
	}
	return out
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

	// Две пары дат разного происхождения, и путать их нельзя.
	//
	// SourceFrom/SourceTo — фактическое окно ИСТОЧНИКА по всей его афише: до
	// какой даты он вообще публикует. Им же ограничен вывод «фильма нет».
	SourceFrom string `json:"sourceFrom,omitempty"`
	SourceTo   string `json:"sourceTo,omitempty"`

	// SaleFrom/SaleTo — окно продаж ИСКОМОГО фильма на этой площадке. Отвечает
	// на вопрос «с какого числа продают», ради которого затеян дальний горизонт.
	SaleFrom string `json:"saleFrom,omitempty"`
	SaleTo   string `json:"saleTo,omitempty"`
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
	// Format нужен сравнению прогонов: отпечаток сеанса его учитывает, и без
	// этого поля 2D и IMAX одного времени были бы одним сеансом.
	Format   string `json:"format,omitempty"`
	PriceMin int    `json:"priceMin,omitempty"`
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
func runProbe(c, tunnel *Client, title, profilePath, previousPath string, days int) {
	film, err := loadFilmProfile(title, profilePath)
	if err != nil {
		fail("%v", err)
	}
	obs, err := readRegistry(os.Stdin)
	if err != nil {
		fail("%v", err)
	}
	// Реестр достраивается записями из кода ДО опроса: иначе площадка с
	// найденным вручную каналом выглядит непокрытой и в опрос не идёт.
	records := applyStandaloneRecords(obs)

	now := time.Now()
	report := ProbeReport{
		FetchedAt: nowRFC3339(),
		Film:      film,
		Days:      days,
		Statuses:  map[string]int{},
		Records:   records,
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
	report.Sales = buildSalesSummary(report.Venues)

	// Сравнение с прошлым прогоном — последним, когда отчёт уже собран целиком.
	if strings.TrimSpace(previousPath) != "" {
		prev, err := readPreviousRun(previousPath)
		if err != nil {
			fail("%v", err)
		}
		d := diffRuns(report, prev)
		report.Diff = &d
	}

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
			Format:     s.Format,
			PriceMin:   s.PriceMin,
		})
	}

	// Фактическое окно источника — по ВСЕЙ его афише, а не по найденному фильму.
	vp.SourceFrom, vp.SourceTo = probe.WindowFrom, probe.WindowTo
	// Окно продаж искомого фильма — по его сеансам.
	vp.SaleFrom, vp.SaleTo = foundWindow(vp.Found)

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
	res = applySourceWindow(res, uncoveredDates(now, days, probe.WindowFrom, probe.WindowTo))
	vp.Status, vp.Evidence, vp.Alive = res.Status, res.Evidence, res.Alive
	return vp
}

// foundWindow — окно продаж искомого фильма: первая и последняя даты сеансов.
func foundWindow(found []FoundShowtime) (string, string) {
	var lo, hi string
	for _, f := range found {
		if len(f.StartsAt) < 10 {
			continue
		}
		day := f.StartsAt[:10]
		if lo == "" || day < lo {
			lo = day
		}
		if hi == "" || day > hi {
			hi = day
		}
	}
	return lo, hi
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

// applySourceWindow запрещает вывод «фильма нет» за пределами того, что источник
// вообще покрыл.
//
// Отдельно от applyHorizonGap, и смешивать их нельзя: там канал ОШИБСЯ, здесь
// ответил успешно, просто дальше своего края не публикует. Написать про такие
// дни «канал не ответил» значило бы соврать, а весь классификатор построен на
// том, что «источник сломался» и «фильма нет» — разные вещи.
//
// Живость не трогается: источник ответил, и счёт покрытия считается по этому
// факту, а не по выводу о фильме.
func applySourceWindow(res ProbeResult, uncovered []string) ProbeResult {
	if res.Status != statusAbsent || len(uncovered) == 0 {
		return res
	}
	res.Status = statusSuspect
	res.Evidence = fmt.Sprintf("источник публикует у́же запрошенного окна, %d дн. вне его охвата (%s): %s",
		len(uncovered), strings.Join(uncovered, ", "), res.Evidence)
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

// readPreviousRun читает отчёт прошлого прогона.
//
// Нечитаемый снимок — отказ прогона, а не пустое сравнение: молча пропустить
// его значило бы напечатать отчёт без раздела «что изменилось» ровно там, где
// его и ждали.
func readPreviousRun(path string) (ProbeReport, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return ProbeReport{}, fmt.Errorf("чтение прошлого прогона: %w", err)
	}
	var prev ProbeReport
	if err := json.Unmarshal(raw, &prev); err != nil {
		return ProbeReport{}, fmt.Errorf("разбор прошлого прогона: %w", err)
	}
	return prev, nil
}
