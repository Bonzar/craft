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
		case otherTownRe.MatchString(v.Address):
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
func collectAggregatorVenues(sessions []YandexSession) []AggregatorVenue {
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
