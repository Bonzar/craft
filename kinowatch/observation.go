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

import "strings"

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
var classPriority = []string{
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
// требовать от музея расписание сеансов бессмысленно.
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

		out = append(out, CinemaObservation{
			Key:    d.Row.ID,
			Name:   d.Row.Company,
			Fields: fields,
		})
	}

	return out
}
