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

	// PRIME CINEMA. Движок etobilet, домен у площадки свой.
	//
	// Вторая строка реестра с тем же названием (10315) канала НЕ получает:
	// сайт оператора знает одну московскую площадку по одному адресу, а второй
	// PRIME CINEMA не нашёлся ни одним источником. Отсутствие второй площадки
	// у оператора — не доказательство того, что строки описывают одни залы,
	// поэтому 10315 остаётся непокрытой, а не объявляется дублём.
	{
		EaisID: "8894", Name: "PRIME CINEMA", Kind: kindEtobilet,
		Params: ChannelParams{pHost: "primecinema.ru"},
		Source: "primecinema.ru/about/ — «Кинотеатр \"Прайм Синема\"», " +
			"адрес: г. Москва, ул. Каховка, 29А, ТРЦ «Prime Plaza», 6 залов",
	},

	// Колибри. Движок p24 общий у нескольких сетей, а домен у каждой свой —
	// без него запрос собрать не из чего, и справочник p24Venues, где хранятся
	// одни uuid, площадку не покрывает.
	//
	// Вторая московская строка реестра — дубль этой (см. duplicateRecords).
	{
		EaisID: "9962", Name: "Колибри Москва", Kind: kindP24,
		Params: ChannelParams{
			pHost:  "colibricinema.ru",
			pVenue: "b57ea270-eda1-4ae4-b1a4-df9eb088f8df",
		},
		Source: "colibricinema.ru/cinema — facility-uuid площадки в разметке страницы контактов, " +
			"адрес: г. Москва, Севастопольский проспект, 11Е",
	},

	// Две московские площадки Синема 5. Какая из них какая, сказал сам источник
	// адресом: `api/v1/cinemas?cinemaIds=<id>` отдаёт название и улицу.
	{
		EaisID: "5382", Name: "Балтика", Kind: kindCinema5,
		Params: ChannelParams{pVenue: "21"},
		Source: "cinema5.ru/api/v1/cinemas?cinemaIds=21 — «Балтика ТРЦ \"Калейдоскоп\"», " +
			"адрес из ответа: г. Москва, ул. Сходненская, д. 56",
	},
	{
		EaisID: "2947", Name: "Киносфера", Kind: kindCinema5,
		Params: ChannelParams{pVenue: "20"},
		Source: "cinema5.ru/api/v1/cinemas?cinemaIds=20 — «Киносфера IMAX ТЦ \"Капитолий\" " +
			"Ленинградский», адрес из ответа: г. Москва, ул. Правобережная, д. 1, корп. Б",
	},

	// Шесть площадок Синема Стар, не сошедшихся со справочником своей сети.
	//
	// Причина расхождения одна на все шесть: реестр называет площадку улицей или
	// станцией метро («Москва Ленинский проспект»), а справочник — вывеской
	// («Синема Стар на Ленинском», «Avenue Sever»). Ключ сравнения имён их не
	// сводит и не должен: сводить «Селигерскую» с «Avenue Sever» по словам
	// нельзя — их роднит адрес, а не название.
	//
	// Поэтому уликой служит адрес из ответа справочника, а не сходство слов. У
	// двух последних адрес прямого совпадения не даёт, и опора там слабее —
	// станция метро в вывеске; сказано в самой записи, чтобы это было видно без
	// раскопок.
	{
		EaisID: "5384", Name: "Синема Стар Москва Ленинский проспект", Kind: kindCinemaStar,
		Params: ChannelParams{pVenue: "na-leninskom"},
		Source: "api.cinemastar.ru/theatre/na-leninskom — «Синема Стар на Ленинском», " +
			"адрес из ответа: г. Москва, Ленинский пр-кт, вл. 109",
	},
	{
		EaisID: "1321", Name: "Синема Стар Москва Дмитровское шоссе", Kind: kindCinemaStar,
		Params: ChannelParams{pVenue: "dmitrovka"},
		Source: "api.cinemastar.ru/theatre/dmitrovka — «Синема Стар Дмитровка», " +
			"адрес из ответа: г. Москва, Дмитровское шоссе, д. 163А",
	},
	{
		EaisID: "8672", Name: "Синема Стар Москва Аминьевское шоссе", Kind: kindCinemaStar,
		Params: ChannelParams{pVenue: "kvartal"},
		Source: "api.cinemastar.ru/theatre/kvartal — «Синема Стар МФК Kvartal West», " +
			"адрес из ответа: Москва, Аминьевское шоссе, д.6",
	},
	{
		EaisID: "10505", Name: "Синема Стар Москва Селигерская", Kind: kindCinemaStar,
		Params: ChannelParams{pVenue: "seligerskaya"},
		Source: "api.cinemastar.ru/theatre/seligerskaya — вывеска «Синема Стар Avenue Sever», " +
			"но идентификатор площадки у самого источника «seligerskaya», " +
			"адрес из ответа: г. Москва, Коровинское шоссе, 2",
	},
	{
		EaisID: "1317", Name: "Синема Стар Москва Академическая", Kind: kindCinemaStar,
		Params: ChannelParams{pVenue: "na-akademicheskoy"},
		Source: "api.cinemastar.ru/theatre/na-akademicheskoy — «Синема Стар на Академической»; " +
			"адрес из ответа (ул. Б. Черемушкинская, д. 1) с названием строки реестра " +
			"не совпадает, опора — станция метро в вывеске, это слабее адреса",
	},
	{
		EaisID: "6321", Name: "Синема Стар Москва Юго-Западная", Kind: kindCinemaStar,
		Params: ChannelParams{pVenue: "avenue"},
		Source: "api.cinemastar.ru/theatre/avenue — «Синема Стар Avenue Southwest»; " +
			"адрес из ответа (пр-т Вернадского, д. 86а) стоит у м. Юго-Западная, " +
			"вывеска называет ту же станцию по-английски — опора слабее прямого " +
			"совпадения улицы",
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

// DuplicateRecord — строка реестра, описывающая ту же площадку, что другая
// строка.
//
// Отличие от клона сети (cloneNetworks) в уровне: там дублируется ЦЕЛАЯ сеть,
// здесь — одна конкретная регистрация. В московском листинге шесть пар
// неразличимых названий, и решается каждая пара порознь, своим источником.
//
// Evidence — не пересказ, а то, чем ИМЕННО доказано совпадение. Без него запись
// неотличима от догадки «названия похожи», а по названию отличить задвоенную
// регистрацию от двух разных залов нельзя.
type DuplicateRecord struct {
	EaisID   string // строка, которая объявляется дублем
	LeaderID string // строка, от которой пишутся сеансы
	Name     string
	Evidence string
}

// duplicateRecords — пары «одна площадка, две регистрации».
//
// Выбор ведущей внутри пары произволен там, где сами строки неразличимы: у них
// одно название, одна сеть и никаких других полей. Произвол безопасен ровно
// потому, что обе строки указывают на одну площадку с одной афишей — но он
// назван вслух, чтобы через год это не выглядело установленным фактом.
var duplicateRecords = []DuplicateRecord{
	{
		EaisID: "10735", LeaderID: "9962", Name: "Колибри Москва",
		Evidence: "colibricinema.ru/cinema — сеть публикует две площадки «Колибри» " +
			"по одному адресу (г. Москва, Севастопольский проспект, 11Е), и движок " +
			"отдаёт им одну афишу: запрос с любым из двух facility-uuid и с заведомо " +
			"несуществующим возвращает одно и то же расписание. " +
			"Какая из двух строк реестра ведущая, источником не определяется — выбрана " +
			"строка с меньшим EaisId",
	},
}

// applyDuplicateRecords помечает дубли и уводит их из опроса.
//
// Возвращает записи, у которых не нашлось строки реестра или ведущей: и то и
// другое — ошибка в самой записи, и молчать о ней нельзя. Дубль без живой
// ведущей хуже непокрытой площадки: он выводит строку из знаменателя, а сеансы
// за неё не пишет никто.
func applyDuplicateRecords(obs []CinemaObservation) (applied int, orphans []string) {
	byID := map[string]int{}
	for i := range obs {
		byID[obs[i].Key] = i
	}

	for _, dr := range duplicateRecords {
		i, ok := byID[dr.EaisID]
		if !ok {
			orphans = append(orphans, dr.EaisID+" ("+dr.Name+"): нет такой строки реестра")
			continue
		}
		if _, ok := byID[dr.LeaderID]; !ok {
			orphans = append(orphans, dr.EaisID+" ("+dr.Name+"): ведущая строка "+dr.LeaderID+" не найдена")
			continue
		}

		obs[i].Fields[fStatusClass] = classCloneOf
		obs[i].Fields[fSourceParams] = "leader=" + dr.LeaderID
		obs[i].Fields[fExcuse] = dr.Evidence
		// Канал дублю не нужен: сеансы за площадку пишет ведущая строка.
		delete(obs[i].Fields, fSourceKind)
		delete(obs[i].Fields, fLastError)
		applied++
	}
	return applied, orphans
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
