package main

// Наблюдения — то, что бинарник отдаёт наружу для записи в Craft.
//
// Бинарник про Craft не знает ничего и писать туда не умеет: коллекции лежат
// вне scope connect-ссылки (проверено — 403 «Document not in scope»), а запись
// свойств идёт MCP-командами, которых у него нет. Поэтому контракт такой:
// на stdin приходит текущее состояние реестра, на stdout уходят наблюдения,
// а раскладывает их по коллекциям рутинная сессия.
//
// Формат наблюдения намеренно плоский «ключ → значения»: рутине не нужно знать
// семантику полей, она делает upsert по Key и не думает.

import (
	"strconv"
	"strings"
)

// CinemaObservation — одна строка реестра в виде, готовом к upsert.
//
// Key — ключ идемпотентности площадки (EaisId). По нему рутина решает, создать
// элемент или обновить существующий; два параллельных прогона с одним ключом
// приходят к одному результату.
type CinemaObservation struct {
	Key    string            `json:"key"`
	Name   string            `json:"name"`
	Fields map[string]string `json:"fields"`
}

// Ключи свойств в Craft — это display-имена, схлопнутые в нижний регистр без
// разделителей: EaisId → eaisid, StatusClass → statusclass. Пишем сразу в
// схлопнутом виде, чтобы не промахнуться: подчёркивания из ключа стираются, и
// last_status ушёл бы мимо laststatus.
const (
	fEaisID       = "eaisid"
	fNetwork      = "network"
	fAddress      = "address"
	fSiteURL      = "siteurl"
	fInsideMkad   = "insidemkad"
	fSourceKind   = "sourcekind"
	fSourceParams = "sourceparams"
	fTier         = "tier"
	fStatusClass  = "statusclass"
	fStatusAt     = "statusat"
	fLastStatus   = "laststatus"
	fLastOk       = "lastok"
	fLastError    = "lasterror"
	fEvidenceURL  = "evidenceurl"
	fNote         = "note"

	// fExcuse — улика, освобождающая площадку от рабочего инструмента.
	//
	// Отдельное поле, а не переиспользование EvidenceURL или LastError, и это
	// несущее решение. Прежняя проверка засчитывала любое непустое поле улики, а
	// LastError уже заполнен у 21 площадки строкой «нет в справочнике
	// собственной сети» — то есть смены класса хватило бы, чтобы они
	// «освободились», не тронув ни одного источника. Поле, которое заполняется
	// ТОЛЬКО записью о факте источника, такой обход закрывает.
	fExcuse = "excuse"

	// Четыре колонки геокодера. GeoAt — время последней ПОПЫТКИ, а не успеха:
	// по нему считается, пора ли перепроверять площадку без координат.
	fLat     = "lat"
	fLon     = "lon"
	fGeoStep = "geostep"
	fGeoAt   = "geoat"
)

// Классы площадки — почему её нельзя опрашивать. Свойство самой площадки,
// меняется редко. Не путать с результатом прогона (LastStatus).
const (
	classUncovered    = "uncovered"      // адаптер ещё не написан
	classSiteUnknown  = "site_unknown"   // сайт не найден машинно
	classGeoUnknown   = "geo_unknown"    // адрес не добыт или не прошёл гейт
	classNoSource     = "no_source"      // домен потерян или отдаёт не кинотеатр
	classNoOnlineSale = "no_online_sale" // сайт есть, билетной системы нет
	classSeasonal     = "seasonal"       // работает только в сезон

	// classClosed — площадка не работает, и об этом сказал сам источник.
	//
	// Отдельно от seasonal: сезонная закрыта по календарю и откроется сама, а
	// эта закрыта по своей причине (ремонт, консервация) на неизвестный срок.
	// Ставится ТОЛЬКО по словам источника, не по нашему выводу — поэтому она и
	// считается законной причиной не иметь рабочего инструмента.
	classClosed = "closed"

	// classCloneOf — запись описывает те же залы, что и другая запись реестра.
	// Ведущая указана в SourceParams. Свойство самого реестра, а не наша
	// неготовность, поэтому строка выводится из знаменателя покрытия: иначе
	// метрика считала бы фантомы, а один физический сеанс писался бы трижды.
	classCloneOf = "clone_of"
)

// classPriority — что побеждает, когда причин совпало несколько.
//
// Деление по ПРИРОДЕ препятствия, а не по удобству метрики: сперва объективные
// свойства площадки (домен мёртв, билетной системы нет, работает только в
// сезон), потом наша неготовность (сайт не нашли, геокод не сошёлся, адаптер не
// написан). Объективное сильнее, потому что оно не зависит от нас: у сезонного
// кинотеатра вне сезона расписания нет ни при каком адаптере.
//
// Первая редакция правила гласила «выигрывает класс, который сохраняет
// знаменатель» — и была неверной, тест это поймал на паре seasonal+uncovered.
// Такое правило искажало бы охват в другую сторону: держало бы в знаменателе
// площадки, которые опросить нельзя в принципе, и покрытие никогда не сходилось
// бы к 100% по причинам вне нашего контроля.
//
// clone_of стоит первым отдельно от этого деления: если строка вообще не
// описывает отдельную площадку, остальные вопросы к ней бессмысленны.
var classPriority = []string{
	classCloneOf,
	classClosed,
	classNoSource,
	classNoOnlineSale,
	classSeasonal,
	classSiteUnknown,
	classGeoUnknown,
	classUncovered,
}

// pickClass выбирает класс из набора причин по приоритету.
// Вторая причина не теряется — вызывающий кладёт её в Note.
func pickClass(reasons ...string) string {
	set := map[string]bool{}
	for _, r := range reasons {
		if r != "" {
			set[r] = true
		}
	}
	for _, c := range classPriority {
		if set[c] {
			return c
		}
	}
	return ""
}

// keepsInDenominator — остаётся ли площадка с таким классом в знаменателе
// покрытия.
//
// Правило: классы про НАШУ неготовность (сайт не нашли, геокод не сошёлся,
// адаптер не написан) знаменатель сохраняют — иначе процент покрытия считался
// бы от удобной выборки и всегда выглядел бы хорошо. Классы про саму площадку
// (мёртвый домен, нет онлайн-продажи, сезонная) из знаменателя исключаются:
// требовать от музея расписание сеансов бессмысленно. Клон исключается по той
// же логике — он не отдельная площадка, а вторая запись об одних и тех же залах.
func keepsInDenominator(class string) bool {
	switch class {
	case classSiteUnknown, classGeoUnknown, classUncovered, "":
		return true
	default:
		return false
	}
}

// buildCinemaObservations превращает разобранный листинг ЕАИС в наблюдения.
//
// IO-free: на вход решения по охвату, на выход готовые записи. Всё, что ещё не
// добыто (адрес, сайт, канал продаж), остаётся пустым — заполняется позже
// геокодером и поиском сайта. Пустое поле при этом не молчит: площадка сразу
// получает класс uncovered, то есть попадает в знаменатель как непокрытая.
func buildCinemaObservations(decisions []ScopeDecision, now string) []CinemaObservation {
	out := make([]CinemaObservation, 0, len(decisions))

	for _, d := range decisions {
		if !d.InScope {
			// Вне охвата по городу (Зеленоград, ТиНАО) — в реестр не пишем
			// вовсе: это не «непокрытая площадка», а другая территория.
			continue
		}

		fields := map[string]string{
			fEaisID:      d.Row.ID,
			fNetwork:     strings.TrimSpace(d.Row.Network),
			fStatusClass: classUncovered,
			fStatusAt:    now,
		}

		// Клон своего канала не получает и в опрос не идёт: сеансы пишутся от
		// ведущей записи. Сам он остаётся в реестре видимой строкой — он
		// законно зарегистрирован, — но с явной причиной и ссылкой на ведущую.
		if leader := cloneLeader(d.Row.Network); leader != "" {
			fields[fStatusClass] = classCloneOf
			fields[fSourceParams] = "leader=" + leader
			fields[fExcuse] = "те же залы описывает запись сети «" + leader + "»"
		}

		// Площадка без сущности «сеанс» — объективное препятствие, а не наша
		// недоработка: требовать расписание от ресторана с приватным залом
		// бессмысленно. Причина пишется словами, чтобы в реестре было видно
		// основание, не заглядывая в код.
		if reason := screeningsAbsentReason(d.Row.Company); reason != "" {
			fields[fStatusClass] = pickClass(fields[fStatusClass], classNoOnlineSale)
			fields[fLastError] = reason
			fields[fExcuse] = reason
		}

		out = append(out, CinemaObservation{
			Key:    d.Row.ID,
			Name:   d.Row.Company,
			Fields: fields,
		})
	}

	return out
}

// addNote добавляет пометку, не затирая уже стоящие.
//
// Note накапливает: пометки от разных шагов живут списком через «;», а слияние
// параллельных прогонов объединяет множества. Перезапись затёрла бы вчерашнее
// предупреждение первой же ежечасной записью, поэтому её здесь нет.
func addNote(fields map[string]string, notes ...string) {
	have := map[string]bool{}
	var order []string
	for _, n := range strings.Split(fields[fNote], ";") {
		n = strings.TrimSpace(n)
		if n == "" || have[n] {
			continue
		}
		have[n] = true
		order = append(order, n)
	}
	for _, n := range notes {
		n = strings.TrimSpace(n)
		if n == "" || have[n] {
			continue
		}
		have[n] = true
		order = append(order, n)
	}
	if len(order) == 0 {
		delete(fields, fNote)
		return
	}
	fields[fNote] = strings.Join(order, "; ")
}

// applyGeo кладёт в наблюдение результат геокодирования.
//
// GeoAt ставится ВСЕГДА, даже когда запросов не было вовсе (сетевая площадка без
// адреса): иначе она вечно подходила бы под условие «раньше не пробовалась» и
// штурмовала бы геокодер каждый час, хотя решение принято без единого запроса.
//
// InsideMkad при неудаче остаётся пустым — ни true, ни false. Пустое значение
// означает «не знаем», и это честно: false молча вычеркнул бы площадку из охвата.
func applyGeo(obs *CinemaObservation, out GeoOutcome, now string) {
	obs.Fields[fGeoAt] = now

	if out.Point == nil {
		obs.Fields[fStatusClass] = pickClass(obs.Fields[fStatusClass], classGeoUnknown)
		obs.Fields[fStatusAt] = now
		if out.Evidence != "" {
			obs.Fields[fEvidenceURL] = out.Evidence
		}
		addNote(obs.Fields, out.Notes...)
		return
	}

	obs.Fields[fLat] = strconv.FormatFloat(out.Point.Lat, 'f', 6, 64)
	obs.Fields[fLon] = strconv.FormatFloat(out.Point.Lon, 'f', 6, 64)
	obs.Fields[fGeoStep] = out.Point.Step
	if out.Point.Address != "" {
		obs.Fields[fAddress] = out.Point.Address
	}
	if out.Point.Evidence != "" {
		obs.Fields[fEvidenceURL] = out.Point.Evidence
	}
	addNote(obs.Fields, out.Notes...)
}

// setEnriched проставляет то, что дал обогатитель, до геокодирования.
// Готовые координаты (у КАРО они есть) снимают нужду в запросах вовсе.
func setEnriched(obs *CinemaObservation, v EnrichedVenue, now string) {
	if v.Address != "" {
		obs.Fields[fAddress] = v.Address
	}
	if v.Website != "" {
		obs.Fields[fSiteURL] = v.Website
	}
	if v.Lat != 0 || v.Lon != 0 {
		obs.Fields[fLat] = strconv.FormatFloat(v.Lat, 'f', 6, 64)
		obs.Fields[fLon] = strconv.FormatFloat(v.Lon, 'f', 6, 64)
		obs.Fields[fGeoStep] = "enricher:" + v.Source
		obs.Fields[fGeoAt] = now
	}
}

// hasCoords — есть ли у наблюдения координаты.
func hasCoords(obs CinemaObservation) bool {
	return obs.Fields[fLat] != "" && obs.Fields[fLon] != ""
}
