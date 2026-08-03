package main

// Каналы, задаваемые записью, а не справочником сети.
//
// Зачем отдельный механизм. Привязка (venues.go) сводит строку реестра со
// справочником её сети. У одиночки справочника нет по определению, а у части
// сетевых площадок нет самой сети в виде каталога — сводить не с чем. Для них
// канал приходится назвать прямо.
//
// Ключ записи — EaisId, а не название. Это существенно: в московском листинге
// шесть пар неразличимых названий («Космик Кинотеатр», «Кинотеатр Киноквартал»,
// «Колибри Москва» и другие), и сопоставление по имени назначило бы обеим
// площадкам пары один и тот же канал. EaisId различает их всегда.
//
// Source — адрес, откуда канал взят. Это улика: без неё запись неотличима от
// догадки, а проверить её через год будет нечем.

import "strings"

// FixedChannel — канал одной конкретной площадки.
type FixedChannel struct {
	EaisID string
	Name   string // как площадка зовётся в реестре, для читаемости записи
	Kind   string
	Params ChannelParams
	Source string
}

// fixedChannels — каналы, найденные поштучно.
//
// Список наполняется ТОЛЬКО по факту живого ответа канала. Запись-заглушка
// здесь была бы худшим из вариантов: в реестре она неотличима от работающей
// площадки, то есть превращала бы непокрытость в мнимое покрытие.
var fixedChannels = []FixedChannel{
	{
		EaisID: "7305", Name: "Silver Cinema", Kind: kindKinoplan,
		Params: ChannelParams{pVenue: "6341"},
		Source: "silvercinema.ru/kosinopark/raspisanie/ — виджет kinowidget.kinoplan.ru/6341",
	},
	{
		EaisID: "6150", Name: "Pushka", Kind: kindPushka,
		Params: ChannelParams{pVenue: "klen"},
		Source: "cinema.pushka.club/moscow/klen — м. Бабушкинская",
	},
	{
		EaisID: "7662", Name: "Pushka Mitino", Kind: kindPushka,
		Params: ChannelParams{pVenue: "ladya"},
		Source: "cinema.pushka.club/moscow/ladya — м. Митино",
	},
	{
		EaisID: "8079", Name: "Pushka Brateevo", Kind: kindPushka,
		Params: ChannelParams{pVenue: "key"},
		Source: "cinema.pushka.club/moscow/key — Братеево, м. Алма-Атинская",
	},
	{
		EaisID: "2697", Name: "ГУМ-Кинотеатр", Kind: kindGum,
		Params: ChannelParams{},
		Source: "gum.ru/kinozal/ — расписание кинозала, не часы работы ТЦ",
	},
}

// applyFixedChannels проставляет наблюдениям каналы из списка.
//
// Возвращает записи, которым не нашлось строки реестра. Потерянная запись — это
// либо опечатка в EaisId, либо площадка, выпавшая из листинга; и то и другое
// должно быть видно, а не проглатываться.
func applyFixedChannels(obs []CinemaObservation) (applied int, orphans []string) {
	byID := map[string]int{}
	for i := range obs {
		byID[obs[i].Key] = i
	}

	for _, fc := range fixedChannels {
		i, ok := byID[fc.EaisID]
		if !ok {
			orphans = append(orphans, fc.EaisID+" ("+fc.Name+")")
			continue
		}
		if skipBinding(obs[i]) {
			continue
		}

		obs[i].Fields[fSourceKind] = fc.Kind
		if p := fc.Params.String(); p != "" {
			obs[i].Fields[fSourceParams] = p
		}
		obs[i].Fields[fEvidenceURL] = fc.Source
		if obs[i].Fields[fStatusClass] == classUncovered {
			delete(obs[i].Fields, fStatusClass)
		}
		delete(obs[i].Fields, fLastError)
		applied++
	}
	return applied, orphans
}

// fixedChannelNames — имена записей для отчёта, в порядке списка.
func fixedChannelNames() string {
	names := make([]string, 0, len(fixedChannels))
	for _, fc := range fixedChannels {
		names = append(names, fc.Name)
	}
	return strings.Join(names, ", ")
}
