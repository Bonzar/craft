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

	// Closed — сказанное самой сетью о том, что площадка не работает.
	//
	// Единственная законная причина не иметь рабочего инструмента, которую
	// нельзя назначить своей пометкой: это слова источника, а не наш вывод.
	// Москино пишет их прямо в названии — «Берёзка (временно закрыт на
	// ремонт)», — и до этого поля приписка молча терялась при нормализации.
	Closed string `json:"closed,omitempty"`
}

// venueClosureWords — по каким словам приписка в названии читается как
// «площадка не работает», а не как уточнение вроде «(Москва)».
var venueClosureWords = []string{"закрыт", "ремонт", "не работает", "временно"}

// venueClosure возвращает приписку о закрытии, если она есть в названии.
func venueClosure(name string) string {
	for _, m := range venueParen.FindAllString(name, -1) {
		note := strings.TrimSpace(strings.Trim(strings.TrimSpace(m), "()"))
		low := strings.ToLower(note)
		for _, w := range venueClosureWords {
			if strings.Contains(low, w) {
				return note
			}
		}
	}
	return ""
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
			ID:     slug,
			Name:   name,
			Kind:   kindMoskino,
			Closed: venueClosure(name),
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

// Адреса справочников площадок. У КАРО справочник один на две задачи: отсюда
// берутся и координаты для геокодера (enrich.go), и идентификаторы каналов.
const (
	cinemaStarVenuesURL = "https://api.cinemastar.ru/data/1"
	moskinoVenuesURL    = "https://mos-kino.ru/cinema/"
	// Сайт Киномакса закрыт капчей Яндекса, а его API — нет: они на разных
	// хостах, и справочник берётся напрямую с api.kinomax.ru.
	kinomaxVenuesURL = "https://api.kinomax.ru/rest/cinemas"
	// Страница расписаний Синема Парка отдаёт московский список сама, без
	// параметра города.
	cinemaParkVenuesURL = "https://kinoteatr.ru/raspisanie-kinoteatrov/"
)

// networkDirectory — один справочник сети: как его достать и как разобрать.
//
// Часть сетей справочника не имеет вовсе — у «Пяти звёзд» и p24 площадки
// перечислены в коде. У них fetch пустой, и это не заглушка: список из трёх
// слагов, живущих только в разметке главной страницы, честнее держать списком,
// чем изображать вокруг него справочник.
type networkDirectory struct {
	Name  string
	URL   string
	Parse func(string) ([]NetworkVenue, error)
	Fixed []NetworkVenue
}

// networkDirectories — справочники, доступные прогону.
//
// Сеть, чьего справочника здесь нет, в привязке не участвует вовсе: её строки
// не получают ни канала, ни вердикта о его отсутствии.
var networkDirectories = []networkDirectory{
	{Name: "КАРО", URL: karoDirectoryURL, Parse: parseKaroVenues},
	{Name: "Синема Стар", URL: cinemaStarVenuesURL, Parse: parseCinemaStarVenues},
	{Name: "Москино", URL: moskinoVenuesURL, Parse: parseMoskinoVenues},
	{Name: "Киномакс", URL: kinomaxVenuesURL, Parse: parseKinomaxVenues},
	{Name: "Синема Парк", URL: cinemaParkVenuesURL, Parse: parseCinemaParkVenues},
	{Name: "Пять звёзд", Fixed: fiveStarsVenues},
	{Name: "p24", Fixed: p24Venues},
}

// NetworkBinding — покрытие одной сети каналами её собственного справочника.
type NetworkBinding struct {
	Network string `json:"network"`
	Venues  int    `json:"venues"`
	BindResult
	Error string `json:"error,omitempty"`
}

// bindAllNetworks проходит справочники сетей и привязывает к ним строки реестра.
//
// Каждая сеть обрабатывается своим вызовом, а не общим котлом из всех
// справочников сразу: строка обязана искать себя только у своей сети, и вердикт
// «нет в справочнике» имеет смысл лишь применительно к конкретному справочнику.
//
// Отказ одного справочника прогон не рушит — сеть остаётся непривязанной с
// причиной в отчёте, ровно как ведут себя обогатители геокодера.
func bindAllNetworks(fetch func(url string) (string, error), obs []CinemaObservation) []NetworkBinding {
	out := make([]NetworkBinding, 0, len(networkDirectories))

	for _, d := range networkDirectories {
		nb := NetworkBinding{Network: d.Name}

		venues := d.Fixed
		if d.URL != "" {
			body, err := fetch(d.URL)
			if err != nil {
				nb.Error = err.Error()
				out = append(out, nb)
				continue
			}
			venues, err = d.Parse(body)
			if err != nil {
				nb.Error = err.Error()
				out = append(out, nb)
				continue
			}
		}

		nb.Venues = len(venues)
		nb.BindResult = bindNetworkVenues(obs, venues)
		out = append(out, nb)
	}

	return out
}

// networkKinds — имя сети в листинге ЕАИС → вид её канала.
//
// Ключ здесь ФРАГМЕНТ нормализованного имени, а не имя целиком: листинг пишет
// сеть как придётся — «КАРО ФИЛЬМ», «СИНЕМА-СТАР», «АО "Киномакс" в г. Москва».
// Сравнение идёт вхождением фрагмента, поэтому все три формы сходятся к одному
// виду канала.
//
// Строки сетей, чьего справочника ещё нет (Mori, Люксор, Мираж, Космик), в
// таблице отсутствуют: вид канала им назначать нечем, и в привязке они не
// участвуют вовсе.
var networkKinds = map[string]string{
	"каро":        kindKaro,
	"москино":     kindMoskino,
	"синема стар": kindCinemaStar,
	"синема парк": kindCinemaPark,
	"киномакс":    kindKinomax,
	"пять звезд":  kind5Zvezd,
	"колибри":     kindP24,
	"премьер зал": kindP24,
}

// kindOfNetwork — вид канала сети, к которой относится строка реестра.
// Пустая строка означает «сеть неизвестна», а не «одиночка».
func kindOfNetwork(network string) string {
	n := normalizeName(network)
	if n == "" {
		return ""
	}
	for frag, kind := range networkKinds {
		if strings.Contains(n, frag) {
			return kind
		}
	}
	return ""
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
//
// Строка ищет себя только в справочнике СВОЕЙ сети, и это не оптимизация.
// Во-первых, ключи разных сетей сталкиваются: «Москино Музеон» и «КАРО Музеон»
// оба дают «музеон», и без разделения по сетям обе строки привязывались бы к
// единственному кандидату «Музеон» из справочника Москино — замерено живьём.
// Во-вторых, вердикт «нет в справочнике собственной сети» иначе доставался бы
// каждой строке чужой сети, просто потому что её справочник сейчас не на руках.
func bindNetworkVenues(obs []CinemaObservation, venues []NetworkVenue) BindResult {
	res := BindResult{}

	haveKind := map[string]bool{}
	byKey := map[string][]NetworkVenue{}
	for _, v := range venues {
		haveKind[v.Kind] = true
		byKey[venueKey(v.Name)] = append(byKey[venueKey(v.Name)], v)
	}

	// Участвуют только строки тех сетей, чьи справочники сейчас на руках.
	var rows []int
	kindOf := map[int]string{}
	for i := range obs {
		if skipBinding(obs[i]) {
			continue
		}
		kind := kindOfNetwork(obs[i].Fields[fNetwork])
		if kind == "" || !haveKind[kind] {
			continue
		}
		rows = append(rows, i)
		kindOf[i] = kind
	}

	// Неразличимость считается ВНУТРИ сети: одинаковый ключ у строк разных
	// сетей — не столкновение, каждая ищет себя в своём справочнике.
	rowCount := map[string]int{}
	for _, i := range rows {
		rowCount[kindOf[i]+"\x00"+venueKey(obs[i].Name)]++
	}

	used := map[string]bool{}
	for _, i := range rows {
		key := venueKey(obs[i].Name)

		if rowCount[kindOf[i]+"\x00"+key] > 1 {
			res.Ambiguous = append(res.Ambiguous, obs[i].Name)
			addNote(obs[i].Fields, noteGeoNameDup)
			continue
		}

		var cands []NetworkVenue
		for _, v := range byKey[key] {
			if v.Kind == kindOf[i] {
				cands = append(cands, v)
			}
		}
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

		// Сеть сама сказала, что площадка не работает. Канал ей не назначаем:
		// опрашивать закрытый кинотеатр нечего, а слова источника — законная
		// причина не иметь инструмента, в отличие от нашей пометки.
		if v.Closed != "" {
			obs[i].Fields[fStatusClass] = classClosed
			obs[i].Fields[fLastError] = "сеть сообщает: " + v.Closed
			obs[i].Fields[fEvidenceURL] = "справочник сети, площадка «" + v.Name + "»"
			used[v.ID] = true
			continue
		}

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
	//
	// «Кино Okko» перечислено трижды, и это не избыточность. Справочник сети
	// пишет вывеску двумя способами сразу — «КИНО Okko Афимолл Сити» латиницей
	// и «Кино Оkkо Щёлковский» вперемешку, — а реестр ЕАИС пишет её кириллицей
	// целиком. Три написания неразличимы на глаз и совершенно различны для
	// сравнения строк.
	"синема парк", "формула кино", "кронверк синема",
	"кино оkkо", "кино okko", "кино окко",
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

// venueCityHead — тот же город, но НЕ в хвосте: Синема-Стар ставит его сразу
// после имени сети — «Синема Стар Москва Европарк» против «Синема Стар
// Европарк» в собственном справочнике сети.
//
// Снимается только когда имя сети действительно снято, и это условие несущее:
// у Миража «Москва» — само название кинотеатра («Москва МАРИ», «Москва
// ОТРАДНОЕ»), и безусловное снятие превратило бы их в «мари» и «отрадное».
var venueCityHead = regexp.MustCompile(`^(?:г\s+)?москва\s+`)

func venueKey(name string) string {
	s := normalizeName(venueParen.ReplaceAllString(name, ""))
	brandCut := false
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
		brandCut = true
		break
	}
	s = venueRank.ReplaceAllString(s, "")
	// Хвост с городом. Справочник Киномакса зовёт площадку «Киномакс-Водный
	// Москва», а реестр — ««Киномакс-Водный» г. Москва»: после нормализации от
	// «г.» остаётся отдельная буква, и снимать надо оба вида хвоста, иначе не
	// совпадёт ни одна площадка сети.
	// Снятие города не имеет права обнулить ключ — ни хвостом, ни серединой.
	// Площадка так и зовётся, «Москва», и пустой ключ вычёркивал бы её из
	// сопоставления молча: и обогатители, и привязка пустой ключ пропускают.
	if cut := strings.TrimSpace(venueCityTail.ReplaceAllString(s, "")); cut != "" {
		s = cut
	}
	if brandCut {
		if cut := strings.TrimSpace(venueCityHead.ReplaceAllString(s, "")); cut != "" {
			s = cut
		}
	}
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
// Страница отдаёт московский список сама, без параметра города, и вместе с ним
// приходят областные площадки (Химки, Зеленопарк). Отсеивать их здесь нечем и
// незачем: охват решает реестр, а лишние площадки видны в отчёте сиротами.
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
