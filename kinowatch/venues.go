package main

// Справочники площадок сетей и привязка к ним строк реестра.
//
// Зачем отдельный шаг. Написанный адаптер сети ещё не означает покрытую
// площадку: адаптеру нужен идентификатор КОНКРЕТНОГО кинотеатра в его канале
// («cinema_id» у КАРО, «ident» у Киномакса, слаг у Москино). Без привязки
// прогон не сможет опросить ни одну из 81 сетевой площадки, хотя все адаптеры
// на месте.
//
// Главное, что выяснила разведка: справочник сети и реестр ЕАИС расходятся В
// ОБЕ СТОРОНЫ. У КАРО в реестре 16 московских площадок, в справочнике 12,
// совпадают 9 — семи (Фили, ВДНХ, Музеон, Эрмитаж, Музей Москвы, под звёздами
// Черемушки, парк Садовники) в канале собственной сети нет вовсе, это летние и
// музейные площадки. Обратно, в справочнике есть три Vegas и Реутов, которых
// нет в московском реестре: они в области.
//
// Поэтому у каждой строки обязан быть явный исход, а площадки справочника без
// пары в реестре не теряются молча — они уходят в отчёт отдельным списком.

import (
	"encoding/json"
	"fmt"
	"html"
	"regexp"
	"strconv"
	"strings"
)

// NetworkVenue — площадка в справочнике её сети.
type NetworkVenue struct {
	// ID — то, что подставляется в запрос расписания. У разных сетей это
	// разное по природе (число, слаг), поэтому строка.
	ID   string `json:"id"`
	Name string `json:"name"`
	// City — если справочник его отдаёт. Пустой означает «сеть не сказала», а
	// не «Москва»: отсев по городу идёт по реестру, а не по догадке.
	City string `json:"city,omitempty"`
	Kind string `json:"kind"`
}

// parseKaroVenues разбирает справочник КАРО.
//
// Переиспользует karoResponse из enrich.go: тот же ответ уже разбирается ради
// адресов, и второй структуры под него заводить незачем.
func parseKaroVenues(body string) ([]NetworkVenue, error) {
	var resp karoResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return nil, fmt.Errorf("разбор справочника КАРО: %w", err)
	}

	out := make([]NetworkVenue, 0, len(resp.Data.Theatre))
	for _, t := range resp.Data.Theatre {
		name := strings.TrimSpace(t.Name)
		if name == "" {
			continue
		}
		out = append(out, NetworkVenue{
			ID:   strconv.Itoa(t.ID),
			Name: name,
			Kind: kindKaro,
		})
	}
	return out, nil
}

// parseKinomaxVenues разбирает список площадок Киномакса.
//
// В запросе расписания участвует не числовой id, а поле ident («vodny»,
// «mozaika»): именно оно уезжает в SourceParams.
func parseKinomaxVenues(body string) ([]NetworkVenue, error) {
	var list []struct {
		ID    int    `json:"id"`
		Ident string `json:"ident"`
		Name  string `json:"name"`
	}
	if err := json.Unmarshal([]byte(body), &list); err != nil {
		return nil, fmt.Errorf("разбор списка площадок Киномакса: %w", err)
	}

	out := make([]NetworkVenue, 0, len(list))
	for _, c := range list {
		ident := strings.TrimSpace(c.Ident)
		name := strings.TrimSpace(c.Name)
		if ident == "" || name == "" {
			continue
		}
		out = append(out, NetworkVenue{ID: ident, Name: name, Kind: kindKinomax})
	}
	return out, nil
}

// moskinoLink — ссылка на страницу площадки вместе с её названием.
//
// Название обязательно, и брать его из слага нельзя: слаг латиницей
// («moskino_kosmos»), а реестр зовёт площадку кириллицей («Москино Космос»).
// Сопоставлять их пришлось бы транслитерацией, то есть гадать. Текст ссылки
// решает задачу точно.
var moskinoLink = regexp.MustCompile(`(?s)href="/cinema/([^"/]+)/"[^>]*>(.{0,150}?)</a>`)

// parseMoskinoVenues разбирает список площадок Москино.
func parseMoskinoVenues(body string) ([]NetworkVenue, error) {
	seen := map[string]bool{}
	var out []NetworkVenue
	for _, m := range moskinoLink.FindAllStringSubmatch(body, -1) {
		slug := m[1]
		name := strings.TrimSpace(html.UnescapeString(stripHTML(m[2])))
		if slug == "" || name == "" || seen[slug] {
			continue
		}
		seen[slug] = true
		out = append(out, NetworkVenue{
			ID:   slug,
			Name: name,
			Kind: kindMoskino,
		})
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("разбор списка площадок Москино: ссылки не найдены (тело %d байт)", len(body))
	}
	return out, nil
}

// BindResult — итог привязки строк реестра к справочнику сети.
//
// Unmatched и Orphans — обе стороны расхождения, и обе обязаны быть видимыми:
// первая означает площадку без канала, вторая — площадку канала, которой нет в
// реестре (область или пропуск в самом реестре).
type BindResult struct {
	Bound     int      `json:"bound"`
	Unmatched []string `json:"unmatched,omitempty"`
	Orphans   []string `json:"orphans,omitempty"`
	Ambiguous []string `json:"ambiguous,omitempty"`
}

// bindNetworkVenues проставляет наблюдениям идентификатор площадки в её канале.
//
// Сопоставление — по нормализованному названию и ТОЛЬКО при единственном
// кандидате с обеих сторон, тем же правилом, что у обогатителей геокодера:
// «ближайший по имени» здесь означал бы, что площадка получит чужое расписание,
// а это отказ хуже пустоты.
//
// Названия площадок сетей несут номер зала спереди («КАРО 7 Атриум» против
// «7 Атриум» в справочнике) и имя сети — сравнение идёт по хвосту, см.
// venueKey.
func bindNetworkVenues(obs []CinemaObservation, venues []NetworkVenue) BindResult {
	res := BindResult{}

	byKey := map[string][]NetworkVenue{}
	for _, v := range venues {
		byKey[venueKey(v.Name)] = append(byKey[venueKey(v.Name)], v)
	}

	// Строки реестра, неразличимые между собой, не привязываются ни одна.
	rowCount := map[string]int{}
	for i := range obs {
		if skipBinding(obs[i]) {
			continue
		}
		rowCount[venueKey(obs[i].Name)]++
	}

	used := map[string]bool{}
	for i := range obs {
		if skipBinding(obs[i]) {
			continue
		}
		key := venueKey(obs[i].Name)

		if rowCount[key] > 1 {
			res.Ambiguous = append(res.Ambiguous, obs[i].Name)
			addNote(obs[i].Fields, noteGeoNameDup)
			continue
		}

		cands := byKey[key]
		if len(cands) != 1 {
			// Площадка ОСТАЁТСЯ непокрытой, а не объявляется непокрываемой.
			//
			// Соблазн поставить сюда no_source велик — и он неверен дважды. По
			// определению этот класс означает «домен потерян или отдаёт не
			// кинотеатр», а домен сети жив и отдаёт кинотеатр: просто данной
			// площадки нет в её справочнике. И, что важнее, no_source выводит
			// строку из знаменателя — семь работающих площадок КАРО (Фили,
			// ВДНХ, Музеон, Эрмитаж, Музей Москвы, под звёздами Черемушки, парк
			// Садовники) перестали бы считаться недоработкой, хотя канал у них
			// скорее всего есть, просто на своей странице.
			//
			// Причина словами отличает эту непокрытость от «адаптер не написан».
			res.Unmatched = append(res.Unmatched, obs[i].Name)
			obs[i].Fields[fStatusClass] = classUncovered
			obs[i].Fields[fLastError] = "нет в справочнике собственной сети — канал искать отдельно"
			continue
		}

		v := cands[0]
		obs[i].Fields[fSourceKind] = v.Kind
		obs[i].Fields[fSourceParams] = "venue=" + v.ID
		// Привязка снимает «адаптер не написан»: канал у площадки теперь есть.
		if obs[i].Fields[fStatusClass] == classUncovered {
			delete(obs[i].Fields, fStatusClass)
		}
		used[v.ID] = true
		res.Bound++
	}

	for _, v := range venues {
		if !used[v.ID] {
			res.Orphans = append(res.Orphans, v.Name)
		}
	}
	return res
}

// skipBinding — строки, которых привязка не касается.
//
// Клон уже несёт clone_of и в опрос не идёт: сеансы за него пишет ведущая
// запись. Площадка без сущности «сеанс» (Коперто, Эльдар) канала не имеет по
// своей природе, и назначать ей идентификатор бессмысленно.
func skipBinding(o CinemaObservation) bool {
	switch o.Fields[fStatusClass] {
	case classCloneOf, classNoOnlineSale:
		return true
	}
	return false
}

// venueRank — ведущий номер зала и знаки препинания, оставшиеся после снятия
// имени сети («КАРО под звёздами: Черемушки» → «: черемушки» → «черемушки»).
var venueRank = regexp.MustCompile(`^[\s:;,.\-–—]*(?:\d+\s+)?[\s:;,.\-–—]*`)

// venueKey приводит название площадки к сравнимому виду.
//
// Сверх обычной нормализации снимаются имя сети и ведущий номер зала: в реестре
// площадка называется «КАРО 7 Атриум», а в справочнике сети — «7 Атриум».
// Сравнение по полному имени не сошлось бы ни на одной площадке.
// venueBrands — имена сетей, снимаемые перед сравнением. Порядок значим:
// более длинное имя идёт раньше, иначе «каро» съест начало «каро под звездами».
var venueBrands = []string{"каро под звездами", "каро", "киномакс", "москино",
	// Четыре вывески одной сети СИНЕМА ПАРК: в реестре площадки записаны под
	// теми же именами, что и в её справочнике.
	"синема парк", "формула кино", "кронверк синема", "кино оkkо", "кино okko",
	"синема стар", "mori cinema", "пять звезд"}

// venueParen — скобочная приписка о состоянии площадки. Сеть пишет её прямо в
// названии («Берёзка (временно закрыт на ремонт)»), и без снятия площадка не
// сопоставится с реестром, где приписки нет.
//
// Снимается ДО нормализации: normalizeName сама убирает скобки как знаки
// препинания, оставляя текст приписки внутри ключа.
var venueParen = regexp.MustCompile(`\s*\([^)]*\)`)

// venueCityTail — хвост с городом в двух видах, какие встречаются живьём:
// «… москва» у справочника Киномакса и «… г москва» у реестра (после
// нормализации «г.» превращается в отдельную букву).
var venueCityTail = regexp.MustCompile(`\s*(?:г\s+)?москва\s*$`)

func venueKey(name string) string {
	s := normalizeName(venueParen.ReplaceAllString(name, ""))
	// Имя сети снимается вместе со ВСЕМ, что стоит перед ним. В реестре
	// площадка бывает записана юридически целиком: «Государственное бюджетное
	// учреждение культуры города Москвы "Московское кино", кинотеатр "Москино
	// Юность"». Значимая часть — хвост после имени сети.
	for _, brand := range venueBrands {
		i := strings.LastIndex(s, brand)
		if i < 0 {
			continue
		}
		tail := strings.TrimSpace(s[i+len(brand):])
		// Название площадки может СОВПАДАТЬ с именем сети целиком — «КАРО под
		// звёздами». Снять бренд значило бы получить пустой ключ, то есть
		// потерять площадку вовсе; в таком случае имя остаётся как есть.
		if tail == "" {
			break
		}
		s = tail
		break
	}
	s = venueRank.ReplaceAllString(s, "")
	// Хвост с городом. Справочник Киномакса зовёт площадку «Киномакс-Водный
	// Москва», а реестр — ««Киномакс-Водный» г. Москва»: после нормализации от
	// «г.» остаётся отдельная буква, и снимать надо оба вида хвоста, иначе не
	// совпадёт ни одна площадка сети.
	s = venueCityTail.ReplaceAllString(s, "")
	return strings.TrimSpace(s)
}

// parseCinemaStarVenues разбирает список площадок Синема-Стар.
//
// Эндпоинт `api.cinemastar.ru/data/1` виден только по запросам фронта: сайт
// сети — SPA, ссылок на площадки в разметке нет вовсе, а угадывание uid по
// одному известному («kvartal») даёт пустые ответы.
//
// Город справочник отдаёт, но отсев по нему здесь не делается: охват решает
// реестр, и второе правило охвата разошлось бы с первым при первой же правке.
func parseCinemaStarVenues(body string) ([]NetworkVenue, error) {
	var resp struct {
		Data struct {
			Theatre []struct {
				UID    string `json:"uid"`
				Name   string `json:"name"`
				CityID int    `json:"city_id"`
			} `json:"theatre"`
		} `json:"data"`
	}
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return nil, fmt.Errorf("разбор списка площадок Синема-Стар: %w", err)
	}

	out := make([]NetworkVenue, 0, len(resp.Data.Theatre))
	for _, t := range resp.Data.Theatre {
		uid := strings.TrimSpace(t.UID)
		name := strings.TrimSpace(t.Name)
		if uid == "" || name == "" {
			continue
		}
		out = append(out, NetworkVenue{ID: uid, Name: name, Kind: kindCinemaStar})
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("разбор списка площадок Синема-Стар: площадок нет (тело %d байт)", len(body))
	}
	return out, nil
}

// cinemaParkLink — слаг площадки рядом с её названием на странице расписаний.
var cinemaParkLink = regexp.MustCompile(`(?s)href="https://kinoteatr\.ru/raspisanie-kinoteatrov/([^"/]+)/"[^>]*>\s*<h3[^>]*>\s*([^<]+?)\s*</h3>`)

// parseCinemaParkVenues разбирает список площадок СИНЕМА ПАРК.
//
// Одна сеть держит четыре вывески — «Синема Парк», «Формула Кино», «Кронверк
// Синема» и «Кино Okko», — и в реестре площадки записаны под ними же. Все
// четыре снимаются перед сравнением списком venueBrands.
//
// Страница доступна только через российский выход: с иностранного адреса хост
// рвёт соединение.
func parseCinemaParkVenues(body string) ([]NetworkVenue, error) {
	seen := map[string]bool{}
	var out []NetworkVenue
	for _, m := range cinemaParkLink.FindAllStringSubmatch(body, -1) {
		slug := m[1]
		name := strings.TrimSpace(html.UnescapeString(stripHTML(m[2])))
		if slug == "" || name == "" || seen[slug] {
			continue
		}
		seen[slug] = true
		out = append(out, NetworkVenue{ID: slug, Name: name, Kind: kindCinemaPark})
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("разбор списка площадок СИНЕМА ПАРК: ссылки не найдены (тело %d байт)", len(body))
	}
	return out, nil
}

// fiveStarsVenues — три московские площадки «Пяти звёзд».
//
// Список в коде, а не в справочнике: у сети его нет, слаги живут только в
// разметке главной страницы. Их три, и ровно столько же строк в реестре.
var fiveStarsVenues = []NetworkVenue{
	{ID: "novokuznetskaya", Name: "Пять звёзд на Новокузнецкой", Kind: kind5Zvezd},
	{ID: "paveletskaya", Name: "Пять звёзд на Павелецкой", Kind: kind5Zvezd},
	{ID: "smolenskaya", Name: "Пять звёзд на Смоленской", Kind: kind5Zvezd},
}

// p24Venues — площадки на движке p24.app.
//
// Идентификатор — uuid из адреса собственного сайта площадки. Найдены два:
// у Нивады («Премьер-Зал») и Колибри. У второго домена Премьер-Зала,
// mirkinomarcos.ru, uuid в разметке нет — его площадки остаются непокрытыми, а
// не подгоняются под чужой идентификатор.
var p24Venues = []NetworkVenue{
	{ID: "041df025-e930-4941-b095-e2639ef8f45f", Name: "Нивада", Kind: kindP24},
	{ID: "b57ea270-eda1-4ae4-b1a4-df9eb088f8df", Name: "Колибри", Kind: kindP24},
}
