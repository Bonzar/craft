package main

// Привязка площадок агрегатора к строкам реестра.
//
// Агрегатор отвечает за фильм и приносит все площадки города разом. Реестр
// отвечает за площадки. Чтобы сеансы второго слоя стали покрытием, а не
// параллельным списком, каждую площадку надо посадить на СВОЮ строку — и это
// единственное место, где второй слой может тихо соврать.
//
// Три правила держат его честным, и каждое стоит на измеренном промахе.
//
// Первое: отсев чужих городов идёт ДО привязки. venueKey снимает из названия
// скобку с городом, поэтому «Silver Cinema (Подольск)» и «Silver Cinema
// (Домодедово)» дают один ключ и садятся на одну строку реестра.
//
// Второе: серая зона по расстоянию. Все верные пары замера уложились в 136
// метров, а дальше пошли ложные — «Киноцентр «Домжур»» в 157 метрах от
// «Художественного» и «Coperto Cinema» в 279 метрах от библиотеки «Москино
// Жуковский». Привязка поэтому обрывается на 150 метрах, а хвост до 300 —
// спорное, не привязка.
//
// Третье: одна строка — одна площадка. Иначе сетки двух разных кинотеатров
// слились бы в одну строку реестра.

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	// attachNearKm — расстояние, на котором привязка считается доказанной.
	attachNearKm = 0.15
	// attachGrayKm — дальше этого точка вообще не рассматривается как кандидат.
	// Между ним и attachNearKm лежит серая зона: там сегодня обе известные
	// ложные пары.
	attachGrayKm = 0.3
	// attachFarKm — точка дальше этого от центра города считается загородной.
	// Грубая мера, потому и не приговор: такая площадка уходит в свою корзину
	// на разбор, а не отбрасывается.
	attachFarKm = 20.0
)

// Центр Москвы для грубой проверки «точка за городом».
const (
	moscowCenterLat = 55.7558
	moscowCenterLon = 37.6173
)

// Корзины непривязанного. Площадка обязана попасть ровно в одну — иначе она
// растворяется, а «данные неполны» — не тот результат, который принимается.
const (
	bucketOutOfScope   = "out-of-scope"   // адрес называет другой населённый пункт
	bucketDisputed     = "disputed"       // серая зона или строка уже занята
	bucketOutsideByGeo = "outside-by-geo" // адрес московский, точка за городом
	bucketUnknown      = "unknown"        // адрес и точка московские, строки нет
)

// otherTownRe — признак «это не Москва в нашем охвате».
//
// Афиша пишет подмосковные адреса с указанием города («г. Мытищи», «Московская
// обл., …»), а московские — без него: город подразумевается. Зеленоград и
// ТиНАО перечислены поимённо: административно это Москва, но охват реестра
// ограничен решением «строго внутри МКАД», и они из него выброшены.
var otherTownRe = regexp.MustCompile(`(?i)(^\s*(г\.|г |пос\.|дер\.|пгт)|` +
	`Московская обл|Моск\. обл|Зеленоград|Крюков|Мамыр|` +
	`Балашиха|Видное|Домодедово|Дзержинск|Жуковский|Ивантеевка|Королёв|Красногорск|` +
	`Краснознаменск|Лобня|Лесной Городок|Люберцы|Мытищи|Одинцово|Подольск|Пушкино|` +
	`Раменское|Реутов|Фрязино|Химки|Щёлково)`)

// moscowPrefixRe — «г. Москва» в начале адреса.
//
// Снимается ДО отсева чужих городов: часть источников пишет город явно, и
// правило «адрес начинается с г.» выбросило бы московскую площадку как чужую.
// Живой промах: «Мягкий кинотеатр Отрада», адрес «г. Москва, Пятницкое шоссе,
// 7-й километр» — Москва, просто за МКАД, и её место в корзине «за городом по
// точке», а не «вне охвата по адресу».
var moscowPrefixRe = regexp.MustCompile(`(?i)^\s*(г\.\s*)?Москва,?\s*`)

// outOfScopeByAddress — называет ли адрес чужой населённый пункт.
func outOfScopeByAddress(addr string) bool {
	return otherTownRe.MatchString(moscowPrefixRe.ReplaceAllString(addr, ""))
}

// AggregatorVenue — площадка агрегатора в том виде, в каком её видит привязка.
type AggregatorVenue struct {
	ID       string  `json:"id"`
	Slug     string  `json:"slug,omitempty"`
	Title    string  `json:"title"`
	Address  string  `json:"address,omitempty"`
	Lat      float64 `json:"lat,omitempty"`
	Lon      float64 `json:"lon,omitempty"`
	Sessions int     `json:"sessions"`
}

// VenueAttachment — привязка одной площадки к строке реестра.
type VenueAttachment struct {
	Venue AggregatorVenue `json:"venue"`
	// RegistryKey — ключ строки реестра (eaisid).
	RegistryKey  string `json:"registryKey"`
	RegistryName string `json:"registryName"`
	// By отвечает на вопрос «чем доказана привязка»: coords или name.
	By string `json:"by"`
	// DistanceM заполняется только у координатной привязки.
	DistanceM int `json:"distanceM,omitempty"`
}

// UnattachedVenue — площадка, не севшая ни на одну строку.
type UnattachedVenue struct {
	Venue  AggregatorVenue `json:"venue"`
	Bucket string          `json:"bucket"`
	// Reason — человекочитаемое объяснение. Без него список корзин
	// превращается в свалку, по которой ничего нельзя решить.
	Reason string `json:"reason"`
}

// AttachResult — итог привязки целиком.
type AttachResult struct {
	Attached   []VenueAttachment `json:"attached"`
	Unattached []UnattachedVenue `json:"unattached"`
}

// attachCandidate — пара «площадка ↔ строка» на рассмотрении.
type attachCandidate struct {
	venueIdx int
	rowIdx   int
	distKm   float64
}

// attachVenues раскладывает площадки агрегатора по строкам реестра.
//
// Порядок шагов значим целиком, менять его нельзя:
//
//  1. отсев чужих городов по адресу;
//  2. отсев загородных по точке — ДО именной ступени, иначе площадка с
//     московским адресом и точкой в области привяжется по имени и в корзину
//     не попадёт вовсе;
//  3. координаты, от ближней пары к дальней;
//  4. имя — только для строк без координат.
func attachVenues(venues []AggregatorVenue, obs []CinemaObservation) AttachResult {
	var res AttachResult

	rowsByKey := map[string][]int{}
	for i, o := range obs {
		rowsByKey[venueKey(o.Name)] = append(rowsByKey[venueKey(o.Name)], i)
	}

	takenRow := map[int]string{}    // строка реестра → идентификатор занявшей площадки
	takenVenue := map[string]bool{} // площадка уже пристроена или отброшена

	drop := func(v AggregatorVenue, bucket, reason string) {
		takenVenue[v.ID] = true
		res.Unattached = append(res.Unattached, UnattachedVenue{Venue: v, Bucket: bucket, Reason: reason})
	}

	// 1-2. Отсев.
	for _, v := range venues {
		switch {
		case outOfScopeByAddress(v.Address):
			drop(v, bucketOutOfScope, "адрес вне охвата «строго внутри МКАД»: "+v.Address)
		case hasPoint(v) && haversineKm(moscowCenterLat, moscowCenterLon, v.Lat, v.Lon) > attachFarKm:
			drop(v, bucketOutsideByGeo, fmt.Sprintf(
				"адрес московский (%s), но точка в %.0f км от центра", v.Address,
				haversineKm(moscowCenterLat, moscowCenterLon, v.Lat, v.Lon)))
		}
	}

	// 3. Координатная ступень: сперва собираем все пары, потом разбираем от
	// ближней к дальней. Жадность тут и есть правило «одна строка — одна
	// площадка»: ближайший претендент занимает строку, остальные спорят.
	var cands []attachCandidate
	for vi, v := range venues {
		if takenVenue[v.ID] || !hasPoint(v) {
			continue
		}
		for ri := range obs {
			lat, lon, ok := rowPoint(obs[ri])
			if !ok {
				continue
			}
			if d := haversineKm(lat, lon, v.Lat, v.Lon); d <= attachGrayKm {
				cands = append(cands, attachCandidate{vi, ri, d})
			}
		}
	}
	sort.Slice(cands, func(a, b int) bool { return cands[a].distKm < cands[b].distKm })

	for _, c := range cands {
		v := venues[c.venueIdx]
		if takenVenue[v.ID] {
			continue
		}
		row := obs[c.rowIdx]

		if c.distKm > attachNearKm {
			drop(v, bucketDisputed, fmt.Sprintf(
				"серая зона: %.0f м до строки %q — дальше 150 м привязка не доказана",
				c.distKm*1000, row.Name))
			continue
		}
		if other, busy := takenRow[c.rowIdx]; busy {
			drop(v, bucketDisputed, fmt.Sprintf(
				"строку %q уже заняла площадка %s, ближе по расстоянию", row.Name, other))
			continue
		}

		takenRow[c.rowIdx] = v.Title
		takenVenue[v.ID] = true
		res.Attached = append(res.Attached, VenueAttachment{
			Venue: v, RegistryKey: row.Key, RegistryName: row.Name,
			By: "coords", DistanceM: int(c.distKm*1000 + 0.5),
		})
	}

	// 4. Именная ступень — только для строк БЕЗ координат. Там, где координаты
	// есть, решение уже принято ими: имя не имеет права его пересматривать.
	byRow := map[int][]int{}
	for vi, v := range venues {
		if takenVenue[v.ID] {
			continue
		}
		var free []int
		for _, ri := range rowsByKey[venueKey(v.Title)] {
			if _, _, ok := rowPoint(obs[ri]); ok {
				continue
			}
			free = append(free, ri)
		}
		if len(free) != 1 {
			continue
		}
		byRow[free[0]] = append(byRow[free[0]], vi)
	}

	for _, ri := range sortedKeys(byRow) {
		vis := byRow[ri]
		row := obs[ri]
		if len(vis) > 1 || takenRow[ri] != "" {
			for _, vi := range vis {
				drop(venues[vi], bucketDisputed, fmt.Sprintf(
					"на строку %q по имени претендует несколько площадок", row.Name))
			}
			continue
		}
		v := venues[vis[0]]
		takenRow[ri] = v.Title
		takenVenue[v.ID] = true
		res.Attached = append(res.Attached, VenueAttachment{
			Venue: v, RegistryKey: row.Key, RegistryName: row.Name, By: "name",
		})
	}

	// Всё, что осталось, — московская площадка, которой реестр не узнал.
	for _, v := range venues {
		if !takenVenue[v.ID] {
			drop(v, bucketUnknown, "реестр не опознал: ключ имени "+venueKey(v.Title)+" ни к чему не ведёт")
		}
	}
	return res
}

// sortedKeys — порядок обхода карты, чтобы прогон был воспроизводим.
func sortedKeys(m map[int][]int) []int {
	out := make([]int, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Ints(out)
	return out
}

func hasPoint(v AggregatorVenue) bool { return v.Lat != 0 || v.Lon != 0 }

// rowPoint — координаты строки реестра, если они есть.
func rowPoint(o CinemaObservation) (float64, float64, bool) {
	lat, err1 := strconv.ParseFloat(strings.TrimSpace(o.Fields[fLat]), 64)
	lon, err2 := strconv.ParseFloat(strings.TrimSpace(o.Fields[fLon]), 64)
	if err1 != nil || err2 != nil {
		return 0, 0, false
	}
	return lat, lon, true
}

// collectAggregatorVenues сворачивает сеансы в список площадок.
func collectAggregatorVenues(sessions []AggregatorSession) []AggregatorVenue {
	idx := map[string]int{}
	var out []AggregatorVenue
	for _, s := range sessions {
		if s.PlaceID == "" {
			continue
		}
		if i, ok := idx[s.PlaceID]; ok {
			out[i].Sessions++
			continue
		}
		idx[s.PlaceID] = len(out)
		out = append(out, AggregatorVenue{
			ID: s.PlaceID, Slug: s.PlaceSlug, Title: s.PlaceTitle,
			Address: s.PlaceAddress, Lat: s.PlaceLat, Lon: s.PlaceLon, Sessions: 1,
		})
	}
	sort.Slice(out, func(a, b int) bool { return out[a].ID < out[b].ID })
	return out
}

// ——— Второй слой в прогоне ———

// AggregatorLayer — итог опроса агрегатора за один прогон.
type AggregatorLayer struct {
	Source string `json:"source"`

	// Film — карточка фильма, который РЕАЛЬНО опрошен. Печатается всегда:
	// по одному расписанию нельзя отличить «фильм нигде не идёт» от «спросили
	// не про тот фильм», и карточка — единственный ответ на второй вопрос.
	Film  YandexEvent `json:"film"`
	FndBy string      `json:"foundBy"` // search | pin

	Sessions int `json:"sessions"`
	Venues   int `json:"venues"`

	Attached   []VenueAttachment `json:"attached"`
	Unattached []UnattachedVenue `json:"unattached"`
	Buckets    map[string]int    `json:"buckets"`

	Agreement AgreementStats `json:"agreement"`

	// Err непустая означает, что слой не отработал ВОВСЕ. Пустой слой без
	// ошибки и сломанный слой — разные вещи: первое значит «фильма нет у
	// агрегатора», второе — «мы до агрегатора не дошли».
	Err string `json:"error,omitempty"`

	// Flaws — изъяны прогона, при которых слой всё же принёс сеансы: не
	// доехавшая дата, не полученные координаты площадки.
	//
	// Отдельно от Err, потому что на Err стоит решение «сеансы слоя не
	// раскладывать». Свалив сюда же одну непришедшую дату, мы выбросили бы из
	// отчёта все остальные — то есть скрыли бы найденное ради ненайденного.
	Flaws []string `json:"flaws,omitempty"`
}

// flaw записывает изъян, не роняя слой.
//
// Копится списком, а не одной строкой: затирание превращало отчёт об изъянах в
// одну случайную беду из нескольких.
func (l *AggregatorLayer) flaw(format string, a ...any) {
	l.Flaws = append(l.Flaws, fmt.Sprintf(format, a...))
}

// AgreementStats — расхождение слоёв по строкам реестра.
//
// Ради этих трёх чисел второй слой и заводился как контроль качества: сколько
// площадок нашёл только свой канал, сколько только агрегатор и сколько оба.
type AgreementStats struct {
	OwnOnly        int `json:"ownOnly"`
	AggregatorOnly int `json:"aggregatorOnly"`
	Both           int `json:"both"`
}

// AggregatorShowtime — сеанс глазами агрегатора.
//
// Лежит РЯДОМ с находками своего канала, а не вместо них: как только поля
// сольются, сравнивать станет не с чем и слой перестанет быть контролем.
type AggregatorShowtime struct {
	StartsAt string `json:"startsAt"`
	Hall     string `json:"hall,omitempty"`
	// SaleStatus — строкой, как её называет источник. Шесть значений в булев
	// признак не сворачиваются.
	SaleStatus string `json:"saleStatus,omitempty"`
	PriceMin   int    `json:"priceMin,omitempty"`
	PriceMax   int    `json:"priceMax,omitempty"`
}

// aggregatorFatal — ошибка, при которой прогон продолжать нельзя.
//
// Таких две: по названию идёт несколько фильмов (гадать нельзя) и карточка
// противоречит профилю (спросили не про тот фильм). Всё остальное — отсутствие
// слоя, и первый слой из-за него страдать не должен.
type aggregatorFatal struct{ err error }

func (e aggregatorFatal) Error() string { return e.err.Error() }

// resolveYandexFilm опознаёт фильм у Афиши: сначала выбирает событие, потом
// открывает его карточку.
func resolveYandexFilm(c *Client, p FilmProfile) (YandexEvent, string, error) {
	if slug := strings.TrimSpace(p.YandexSlug); slug != "" {
		id, _, err := yandexEventID(c, slug)
		if err != nil {
			return YandexEvent{}, "pin", err
		}
		ev, _, err := fetchYandexEventCard(c, id)
		if err != nil {
			return YandexEvent{}, "pin", err
		}
		return ev, "pin", nil
	}

	found, _, err := findYandexEvents(c, p.Title)
	if err != nil {
		return YandexEvent{}, "search", err
	}
	ev, err := pickYandexEvent(found, p)
	if err != nil {
		// Ноль кандидатов — законный ответ: фильм ещё не вышел или уже сошёл.
		// Слоя в этом прогоне просто нет. А вот несколько кандидатов — вопрос
		// к Владу, и молча выбирать за него нельзя.
		if len(found) > 1 {
			return YandexEvent{}, "search", aggregatorFatal{err}
		}
		return YandexEvent{}, "search", err
	}
	return ev, "search", nil
}

// runYandexLayer опрашивает Афишу и раскладывает её площадки по реестру.
//
// Вторым значением отдаёт сами сеансы: они нужны прогону, чтобы разложить их
// по строкам реестра уже после обхода собственных касс.
func runYandexLayer(c *Client, film FilmProfile, obs []CinemaObservation, from time.Time, days int) (*AggregatorLayer, []AggregatorSession, error) {
	layer := &AggregatorLayer{Source: "yandex-afisha", Buckets: map[string]int{}}

	ev, by, err := resolveYandexFilm(c, film)
	layer.FndBy = by
	if err != nil {
		layer.Err = err.Error()
		if fatal, ok := err.(aggregatorFatal); ok {
			return layer, nil, fatal
		}
		return layer, nil, nil
	}
	layer.Film = ev

	sessions, _, err := fetchYandexScheduleByID(c, ev.ID, from, days)
	if err != nil {
		layer.Err = err.Error()
		return layer, nil, nil
	}
	layer.Sessions = len(sessions)

	// Сверка карточки идёт ПОСЛЕ расписания: сама по себе пустота законна, а
	// вот пустота при несверенной карточке — это ловушка однофамильца.
	if err := verifyYandexEvent(ev, film, len(sessions)); err != nil {
		layer.Err = err.Error()
		return layer, nil, aggregatorFatal{err}
	}

	venues := collectAggregatorVenues(sessions)
	layer.Venues = len(venues)

	res := attachVenues(venues, obs)
	layer.Attached, layer.Unattached = res.Attached, res.Unattached
	for _, u := range res.Unattached {
		layer.Buckets[u.Bucket]++
	}
	return layer, sessions, nil
}

// aggregatorShowtimesByRow раскладывает сеансы агрегатора по ключам реестра.
func aggregatorShowtimesByRow(layer *AggregatorLayer, sessions []AggregatorSession) map[string][]AggregatorShowtime {
	rowOf := map[string]string{}
	for _, a := range layer.Attached {
		rowOf[a.Venue.ID] = a.RegistryKey
	}

	out := map[string][]AggregatorShowtime{}
	for _, s := range sessions {
		key, ok := rowOf[s.PlaceID]
		if !ok {
			continue
		}
		out[key] = append(out[key], AggregatorShowtime{
			StartsAt: s.StartsAt, Hall: s.Hall,
			SaleStatus: s.SaleStatus, PriceMin: s.PriceMin, PriceMax: s.PriceMax,
		})
	}
	for k := range out {
		sort.Slice(out[k], func(a, b int) bool { return out[k][a].StartsAt < out[k][b].StartsAt })
	}
	return out
}

// countAgreement считает расхождение слоёв по строкам реестра.
//
// Считается по СТРОКАМ, а не по площадкам агрегатора: непривязанная площадка
// живёт в своей корзине и в «только агрегатор» не попадает — иначе одно число
// смешало бы «наш канал промолчал» с «это вообще не наша площадка».
func countAgreement(ownFound map[string]bool, byRow map[string][]AggregatorShowtime) AgreementStats {
	var st AgreementStats
	seen := map[string]bool{}

	for key, own := range ownFound {
		seen[key] = true
		agg := len(byRow[key]) > 0
		switch {
		case own && agg:
			st.Both++
		case own:
			st.OwnOnly++
		case agg:
			st.AggregatorOnly++
		}
	}
	for key := range byRow {
		if !seen[key] && len(byRow[key]) > 0 {
			st.AggregatorOnly++
		}
	}
	return st
}

// ——— Слой kinoafisha ———

// runKinoafishaLayer опрашивает kinoafisha по фильму на окне прогона.
//
// Отличие от Яндекса в цене и в форме: там одно окно приходит одним ответом,
// тут первичная страница плюс запрос на каждую недостающую дату.
func runKinoafishaLayer(c *Client, film FilmProfile, obs []CinemaObservation, from time.Time, days int) (*AggregatorLayer, []AggregatorSession, error) {
	layer := &AggregatorLayer{Source: "kinoafisha", Buckets: map[string]int{}}

	id, err := resolveKinoafishaFilm(c, film)
	layer.FndBy = "search"
	if err != nil {
		layer.Err = err.Error()
		if fatal, ok := err.(aggregatorFatal); ok {
			return layer, nil, fatal
		}
		return layer, nil, nil
	}
	layer.Film = YandexEvent{ID: id, Slug: id, Title: film.Title}

	page, err := fetchKinoafishaMovie(c, id)
	if err != nil {
		layer.Err = err.Error()
		return layer, nil, nil
	}

	sessions, perr := parseKinoafisha(page, "")
	if perr != nil {
		layer.Err = perr.Error()
		return layer, nil, nil
	}

	// Первичная страница наливает целиком только первую дату — остальные
	// догружаются. Даты вне окна прогона не трогаем: слой обязан идти по тому
	// же окну, что первый, иначе счётчик расхождений считает разницу окон.
	seen := map[string]bool{}
	for _, s := range sessions {
		if len(s.StartsAt) >= 10 {
			seen[s.StartsAt[:10]] = true
		}
	}
	for date, skip := range kinoafishaPending(page) {
		if !withinWindow(date, from, days) {
			continue
		}
		more, err := fetchKinoafishaDate(c, id, date, skip)
		if err != nil {
			// Отказ ОДНОЙ даты слой не рушит, но и молчать о нём нельзя:
			// иначе неполнота выглядит как «на эту дату сеансов нет».
			layer.flaw("дата %s не догружена: %v", date, err)
			continue
		}
		rest, err := parseKinoafisha(more, date)
		if err != nil {
			layer.flaw("дата %s не разобрана: %v", date, err)
			continue
		}
		sessions = append(sessions, rest...)
	}

	// Сеансы вне окна прогона отбрасываются уже после сбора: страница отдаёт
	// свой горизонт целиком, а сравнивать слои можно только на общем окне.
	sessions = sessionsWithinWindow(sessions, from, days)
	layer.Sessions = len(sessions)

	venues := collectAggregatorVenues(sessions)
	layer.Venues = len(venues)

	geo := newKinoafishaGeo(c)
	for i := range venues {
		lat, lon, err := geo.point(venues[i].Slug)
		if err != nil {
			// Площадка без точки не выбрасывается — её ещё может опознать имя.
			// Но промолчать нельзя: непривязанная площадка выглядела бы как
			// «реестр её не знает», хотя причина в несостоявшемся запросе.
			layer.flaw("координаты %q не получены: %v", venues[i].Title, err)
			continue
		}
		venues[i].Lat, venues[i].Lon = lat, lon
	}

	res := attachVenues(venues, obs)
	layer.Attached, layer.Unattached = res.Attached, res.Unattached
	for _, u := range res.Unattached {
		layer.Buckets[u.Bucket]++
	}
	return layer, sessions, nil
}

// kinoafishaGeo — координаты площадок, взятые один раз на прогон.
//
// Точка лежит на странице кинотеатра, а не в расписании, поэтому за каждой
// нужен свой запрос. Кэш держит и промах тоже: повторный заход за тем же
// отказом стоит ровно столько же, сколько первый, а источник считает частоту.
type kinoafishaGeo struct {
	// fetch отделён от клиента, чтобы кэш проверялся без сети.
	fetch func(id string) (float64, float64, error)
	seen  map[string]kinoafishaPoint
}

type kinoafishaPoint struct {
	lat, lon float64
	err      error
}

func newKinoafishaGeo(c *Client) *kinoafishaGeo {
	return &kinoafishaGeo{fetch: func(id string) (float64, float64, error) {
		return fetchKinoafishaVenueGeo(c, id)
	}}
}

func (g *kinoafishaGeo) point(id string) (float64, float64, error) {
	if id == "" {
		return 0, 0, fmt.Errorf("kinoafisha: у площадки нет идентификатора страницы")
	}
	if p, ok := g.seen[id]; ok {
		return p.lat, p.lon, p.err
	}

	lat, lon, err := g.fetch(id)
	if g.seen == nil {
		g.seen = map[string]kinoafishaPoint{}
	}
	g.seen[id] = kinoafishaPoint{lat, lon, err}
	return lat, lon, err
}

// resolveKinoafishaFilm выбирает фильм из выдачи поиска.
//
// Правило то же, что у Яндекса, но кандидатов сужает уже сам поиск: он
// каталожный, и точное совпадение названия отсекается в клиенте. Остаток —
// одноимённые фильмы разных лет, и выбирать между ними за Влада нельзя.
func resolveKinoafishaFilm(c *Client, p FilmProfile) (string, error) {
	found, err := findKinoafishaMovies(c, p.Title)
	if err != nil {
		return "", err
	}
	switch len(found) {
	case 0:
		return "", fmt.Errorf("kinoafisha: по названию %q нет ни одного фильма", p.Title)
	case 1:
		return found[0].ID, nil
	}

	// Год есть прямо в карточке выдачи, поэтому развилка решается им, а не
	// вторым запросом. Живой случай: «Майкл» — три фильма, 2026, 2023 и 1996.
	if p.Year > 0 {
		var byYear []KinoafishaMovie
		for _, m := range found {
			if m.Year == p.Year {
				byYear = append(byYear, m)
			}
		}
		if len(byYear) == 1 {
			return byYear[0].ID, nil
		}
	}

	names := make([]string, 0, len(found))
	for _, m := range found {
		names = append(names, fmt.Sprintf("%s (%d, id %s)", m.Title, m.Year, m.ID))
	}
	return "", aggregatorFatal{fmt.Errorf(
		"kinoafisha: по названию %q идёт несколько фильмов, укажи год в профиле: %s",
		p.Title, strings.Join(names, "; "))}
}

// withinWindow — попадает ли дата в окно прогона.
func withinWindow(date string, from time.Time, days int) bool {
	if days < 1 {
		days = 1
	}
	lo := from.Format("2006-01-02")
	hi := from.AddDate(0, 0, days-1).Format("2006-01-02")
	return date >= lo && date <= hi
}

// sessionsWithinWindow оставляет сеансы внутри окна прогона.
func sessionsWithinWindow(in []AggregatorSession, from time.Time, days int) []AggregatorSession {
	out := in[:0]
	for _, s := range in {
		if len(s.StartsAt) >= 10 && withinWindow(s.StartsAt[:10], from, days) {
			out = append(out, s)
		}
	}
	return out
}
