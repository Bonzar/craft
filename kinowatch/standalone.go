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
		EaisID: "195", Name: "Художественный", Kind: kindHudozhestvenny,
		Params: ChannelParams{},
		Source: "cinema1909.ru/schedule/<дата> — расписание в данных страницы",
	},
	{
		EaisID: "2697", Name: "ГУМ-Кинотеатр", Kind: kindGum,
		Params: ChannelParams{},
		Source: "gum.ru/kinozal/ — расписание кинозала, не часы работы ТЦ",
	},

	{
		EaisID: "10617", Name: "Кинотеатр «МИР»", Kind: kindPremierzal,
		Params: ChannelParams{pHost: "mirkinomarcos.ru"},
		Source: "mirkinomarcos.ru/schedule — виджет widget.premierzal.ru на сайте площадки",
	},
	{
		EaisID: "6130", Name: "МОРИ Синема Кунцево", Kind: kindMori,
		Params: ChannelParams{pVenue: "1"},
		Source: "mori.film/cities-list — Москва, МФК Кунцево Плаза",
	},

	// Три московские площадки Миража. У каждой свой адрес расписания; общая
	// страница города отдаёт только MARI, поэтому идентификатор обязателен.
	{
		EaisID: "6156", Name: "Москва МАРИ", Kind: kindMirage,
		Params: ChannelParams{pVenue: "18"},
		Source: "mirage.ru/msk/cinema/18/ — MARI, ул. Поречная, 10",
	},
	{
		EaisID: "8071", Name: "Москва ОТРАДНОЕ", Kind: kindMirage,
		Params: ChannelParams{pVenue: "23"},
		Source: "mirage.ru/msk/cinema/23/ — FORT ОТРАДНОЕ, ул. Декабристов, 12",
	},
	{
		EaisID: "8320", Name: "Москва РОСТОКИНО", Kind: kindMirage,
		Params: ChannelParams{pVenue: "24"},
		Source: "mirage.ru/msk/cinema/24/ — ЕВРОПОЛИС, Проспект Мира, 211к2",
	},

	// Две площадки Киноквартала. В реестре обе записаны одинаково — «Кинотеатр
	// Киноквартал», без адреса, — поэтому какая строка какому залу
	// соответствует, по листингу не решить. Обе получают рабочий канал, а
	// непроверенность привязки сказана прямо: скрытая догадка хуже открытой.
	{
		EaisID: "6673", Name: "Кинотеатр Киноквартал", Kind: kindKinoplan,
		Params: ChannelParams{pVenue: "2402"},
		Source: "витрина Кинокассы, «Киноквартал - Москва (Ясенево)»; " +
			"пара строк реестра неразличима, привязка к конкретному залу не проверена",
	},
	{
		EaisID: "6309", Name: "Кинотеатр Киноквартал", Kind: kindKinoplan,
		Params: ChannelParams{pVenue: "2709"},
		Source: "витрина Кинокассы, «Киноквартал в ТЦ Варшавский»; " +
			"пара строк реестра неразличима, привязка к конкретному залу не проверена",
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
