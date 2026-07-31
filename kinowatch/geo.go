package main

// Геокодер: превращает строку реестра в координаты.
//
// ЕАИС не даёт ни адреса, ни координат, поэтому адрес здесь не вход, а результат:
// сперва его пытаются дать обогатители (справочник КАРО, объекты OSM), и только
// потом за дело берётся каскад из четырёх ступеней. Порядок ступеней не
// косметика: поиск по названию площадки стоит последним, потому что путает
// филиалы одной сети — фолбек по имени однажды притянул «Кронверк Вэйпарк» к
// «Кронверк Облака», промах 35 км.
//
// Отсюда же главное правило файла: ошибочная точка хуже пустой. Ответ, не
// прошедший гейт правдоподобия, отбрасывается, площадка получает geo_unknown и
// остаётся в знаменателе покрытия — то есть мозолит глаза как невыполненная
// работа, а не исчезает с ложными координатами.

import (
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

// Ступени каскада. Значение пишется в реестр (колонка GeoStep), поэтому
// строки — часть контракта с Craft, а не внутренние имена.
const (
	stepPhotonRaw     = "photon-raw"     // адрес как есть из обогатителя
	stepNominatimNorm = "nominatim-norm" // адрес, очищенный до «Москва, улица, дом»
	stepPhotonMall    = "photon-mall"    // название торгового центра из адреса
	stepPhotonTitle   = "photon-title"   // название площадки + Москва
)

// Пометки для Note — фиксированные константы, а не свободный текст: слияние
// параллельных прогонов объединяет множества строк, и «сайт не найден» с «сайт
// не нашли» стали бы двумя разными пометками.
const (
	noteGeoUnverified   = "geo:unverified"
	noteGeoNameDup      = "geo:name-duplicate"
	noteGeoOutsideByDis = "geo:outside-mkad-by-district"
)

// crossCheckLimitKm — расхождение с проверочным геокодером, после которого оба
// ответа отбрасываются.
const crossCheckLimitKm = 2.0

// EnrichedVenue — площадка из источника-обогатителя: у неё уже есть адрес, а
// иногда и готовые координаты.
type EnrichedVenue struct {
	Name    string  `json:"name"`
	Address string  `json:"address"`
	Lat     float64 `json:"lat"`
	Lon     float64 `json:"lon"`
	Source  string  `json:"source"` // karo | osm
	// Website приходит попутно в тегах OSM. Поиском сайта первая поставка не
	// занимается (шаг вынесен за неё), но выбрасывать уже полученное незачем.
	Website string `json:"website,omitempty"`
}

// GeoPoint — итоговая точка площадки.
type GeoPoint struct {
	Lat     float64 `json:"lat"`
	Lon     float64 `json:"lon"`
	Step    string  `json:"step"`
	Address string  `json:"address,omitempty"`
	// Evidence — URL запроса, давшего точку. У неудачи это URL последней
	// попытки: без него «не сошлось» невозможно перепроверить руками.
	Evidence string `json:"evidence,omitempty"`
}

// GeoOutcome — результат геокодирования одной площадки.
type GeoOutcome struct {
	Point *GeoPoint `json:"point,omitempty"`
	Notes []string  `json:"notes,omitempty"`
	// Evidence заполняется и при неудаче — URL последнего запроса.
	Evidence string `json:"evidence,omitempty"`
	// Errors — отказы геокодера по ступеням. Копятся отдельно от «ответ есть,
	// но гейт не пропустил»: это разные вещи, и без разделения «сервис молчит»
	// выглядит как «площадки не существует».
	Errors []string `json:"errors,omitempty"`
}

// planSteps — какие ступени положены площадке.
//
// Сетевая площадка без адреса не геокодируется вовсе: единственная доступная ей
// ступень — поиск по названию, а именно он и путает филиалы одной сети. Пока
// разведка сетей не даст адреса, честное «не знаю» лучше координат соседнего
// филиала.
func planSteps(hasAddress, isNetwork bool) []string {
	switch {
	case hasAddress && !isNetwork:
		return []string{stepPhotonRaw, stepNominatimNorm, stepPhotonMall, stepPhotonTitle}
	case hasAddress && isNetwork:
		return []string{stepPhotonRaw, stepNominatimNorm, stepPhotonMall}
	case !hasAddress && !isNetwork:
		return []string{stepPhotonTitle}
	default:
		return nil
	}
}

// needsCrossCheck — ступени, где искали по названию, а не по адресу: только они
// рискуют попасть в чужой филиал, и только их ответы проверяются вторым
// геокодером. Кросс-чек не бесплатен, поэтому он прицельный.
func needsCrossCheck(step string) bool {
	return step == stepPhotonMall || step == stepPhotonTitle
}

// Маркеры торговых центров. Список закрытый: по нему и вырезается название ТЦ из
// адреса, и собирается запрос ступени photon-mall.
var mallMarkerRe = regexp.MustCompile(`(?i)(^|[\s,.])(ТРЦ|ТРК|ТЦ|МФК|БЦ|ТК)([\s,.]|$)`)

// Сегменты адреса, которые Nominatim только сбивают: корпус, строение, владение,
// этаж, помещение. Литера дома сюда НЕ входит — «61А» это другой дом, чем «61»,
// и снятие литеры превращает попадание в промах.
var auxSegRe = regexp.MustCompile(`(?i)^(корп|кор|к|стр|с|влад|вл|этаж|эт|пом|оф)\.?\s*\d+[а-я]?$`)

// Название в кавычках — «Калужский», "Авиапарк". Для Nominatim это мусор, для
// ступени mall — наоборот, единственное полезное.
var quotedRe = regexp.MustCompile(`[«"']([^»"']+)[»"']`)

var cityPrefixRe = regexp.MustCompile(`(?i)^(г\.?\s*)?москва$`)

// moscowNameRe — Москва как её пишут геокодеры.
//
// Латинское «Moscow» тут не паранойя: Photon переключается на английский от
// заголовка Accept-Language, и гейт, знающий только кириллицу, начинает
// отбраковывать вообще всё (ровно это и случилось при первом прогоне). Заголовок
// у гео-клиента снят, но гейт не должен висеть на одной этой ниточке.
var moscowNameRe = regexp.MustCompile(`(?i)^(г\.?\s*)?(москва|moscow)$`)

// normalizeAddress приводит адрес к виду «Москва, улица, дом».
//
// Без очистки Nominatim берёт 1 адрес из 12 вместо 8 — он строг к строке и
// спотыкается о названия ТЦ и корпуса.
func normalizeAddress(raw string) string {
	segs := strings.Split(raw, ",")
	kept := make([]string, 0, len(segs))

	for _, seg := range segs {
		s := strings.TrimSpace(seg)
		if s == "" {
			continue
		}
		// Город снимаем всегда и подставляем сами: в исходных адресах он то
		// «Москва», то «г. Москва», то отсутствует вовсе.
		if cityPrefixRe.MatchString(s) {
			continue
		}
		if mallMarkerRe.MatchString(s) {
			continue
		}
		if auxSegRe.MatchString(s) {
			continue
		}
		// Сегмент целиком в кавычках — название объекта, не адрес.
		if q := quotedRe.FindStringSubmatch(s); q != nil && strings.TrimSpace(quotedRe.ReplaceAllString(s, "")) == "" {
			continue
		}
		kept = append(kept, s)
	}

	if len(kept) == 0 {
		return "Москва"
	}
	return "Москва, " + strings.Join(kept, ", ")
}

// extractMall достаёт название торгового центра из адреса.
// Пусто — значит ступень photon-mall для этой площадки не исполняется.
func extractMall(raw string) string {
	for _, seg := range strings.Split(raw, ",") {
		s := strings.TrimSpace(seg)
		if !mallMarkerRe.MatchString(s) {
			continue
		}
		// Название в кавычках точнее остатка строки.
		if q := quotedRe.FindStringSubmatch(s); q != nil {
			return strings.TrimSpace(q[1]) + ", Москва"
		}
		name := strings.TrimSpace(mallMarkerRe.ReplaceAllString(s, " "))
		if name != "" {
			return name + ", Москва"
		}
	}
	return ""
}

// titleQuery — запрос последней ступени.
func titleQuery(name string) string {
	return strings.TrimSpace(name) + ", Москва"
}

// haversineKm — расстояние между точками по земной поверхности.
func haversineKm(lat1, lon1, lat2, lon2 float64) float64 {
	const earthKm = 6371.0
	rad := func(d float64) float64 { return d * math.Pi / 180 }
	dLat := rad(lat2 - lat1)
	dLon := rad(lon2 - lon1)
	a := math.Sin(dLat/2)*math.Sin(dLat/2) +
		math.Cos(rad(lat1))*math.Cos(rad(lat2))*math.Sin(dLon/2)*math.Sin(dLon/2)
	return 2 * earthKm * math.Asin(math.Sqrt(a))
}

// photonFeature — ответ Photon в том виде, в каком он реально приходит
// (проверено живым запросом 31.07).
//
// Внимание на поле Type: у Photon это УРОВЕНЬ ТОЧНОСТИ (house, street, district,
// city, country), а не вид объекта — вид лежит в OsmKey/OsmValue. По запросу
// «Кинотеатр Иллюзион, Москва» приходит автобусная остановка с Type=house:
// уровень точности адресный, объект посторонний. Поэтому гейт по Type отсекает
// только слишком грубые ответы, а совпадение объекта проверяет кросс-чек.
type photonFeature struct {
	Properties struct {
		OsmKey   string `json:"osm_key"`
		OsmValue string `json:"osm_value"`
		Type     string `json:"type"`
		Name     string `json:"name"`
		Street   string `json:"street"`
		House    string `json:"housenumber"`
		District string `json:"district"`
		City     string `json:"city"`
		State    string `json:"state"`
		Country  string `json:"country"`
	} `json:"properties"`
	Geometry struct {
		Coordinates []float64 `json:"coordinates"` // [lon, lat]
	} `json:"geometry"`
}

type photonResponse struct {
	Features []photonFeature `json:"features"`
}

type nominatimPlace struct {
	Lat         string `json:"lat"`
	Lon         string `json:"lon"`
	AddressType string `json:"addresstype"`
	PlaceRank   int    `json:"place_rank"`
	DisplayName string `json:"display_name"`
	Address     struct {
		City  string `json:"city"`
		Town  string `json:"town"`
		State string `json:"state"`
	} `json:"address"`
}

// Уровни ответа, которые адресом не являются: район, город, регион, страна.
// Точка «где-то в Москве» хуже пустоты — она молча уводит площадку не туда.
var tooCoarsePhoton = map[string]bool{
	"district": true,
	"city":     true,
	"county":   true,
	"state":    true,
	"country":  true,
	"locality": true,
	"other":    true,
}

// isMoscow — город в ответе обязан быть Москвой. Это защита от коллизий по
// названию: «Радуга кино» уезжает в Магадан, «Дружба» на Камчатку (обе проверены).
func isMoscow(city, state string) bool {
	return moscowNameRe.MatchString(strings.TrimSpace(city)) ||
		moscowNameRe.MatchString(strings.TrimSpace(state))
}

// photonPlausible — прошёл ли ответ Photon гейт правдоподобия.
func photonPlausible(f photonFeature) bool {
	if len(f.Geometry.Coordinates) != 2 {
		return false
	}
	if !isMoscow(f.Properties.City, f.Properties.State) {
		return false
	}
	return !tooCoarsePhoton[strings.ToLower(f.Properties.Type)]
}

// nominatimPlausible — то же для Nominatim. Уровень точности там числом:
// place_rank у здания 30, у города около 16, поэтому граница 20 отделяет
// адресный ответ от «города целиком».
func nominatimPlausible(p nominatimPlace) bool {
	if p.PlaceRank < 20 {
		return false
	}
	city := p.Address.City
	if city == "" {
		city = p.Address.Town
	}
	return isMoscow(city, p.Address.State)
}

func (p nominatimPlace) point() (float64, float64, bool) {
	lat, errLat := strconv.ParseFloat(p.Lat, 64)
	lon, errLon := strconv.ParseFloat(p.Lon, 64)
	if errLat != nil || errLon != nil {
		return 0, 0, false
	}
	return lat, lon, true
}

const (
	photonBase    = "https://photon.komoot.io/api/"
	nominatimBase = "https://nominatim.openstreetmap.org/search"
)

// photonURL — запрос к Photon.
// Параметр lang=ru не ставим сознательно: Photon его не поддерживает и отвечает
// «Language is not supported», а выглядит это как «ничего не найдено».
func photonURL(base, query string) string {
	return fmt.Sprintf("%s?q=%s&limit=3", strings.TrimRight(base, "?"), url.QueryEscape(query))
}

func nominatimURL(base, query string) string {
	return fmt.Sprintf("%s?q=%s&format=jsonv2&addressdetails=1&limit=3",
		base, url.QueryEscape(query))
}

// nameMatches — совпадает ли имя объекта в ответе с тем, что искали.
//
// Проверка нужна ровно на ступени поиска по названию: Photon возвращает не «то,
// что вы назвали, или ничего», а ближайшее похожее. По запросу «HIGHENDER
// CINEMA, Москва» первым приходит «Prime Cinema» — московский кинотеатр,
// адресного уровня, гейт по городу и типу проходит насквозь, а площадка чужая
// (проверено живым запросом 31.07). Без сверки имён каскад раздавал бы соседние
// кинотеатры вместо честного «не знаю».
//
// Сравнение — равенство имён, очищенных от родовых слов. Свободная вложенность
// («одно имя содержит другое») здесь не годится, и это выяснил живой прогон:
// по запросу «Pushka Mitino, Москва» Photon отдал объект с именем «Pushka», а
// по «Pushka Brateevo» — его же. Три площадки сети получили одну точку, причём
// с виду достоверную. Разница между «Кинотеатр Иллюзион» ↔ «Иллюзион» и
// «Pushka Mitino» ↔ «Pushka» именно в том, ЧТО лишнее: в первом случае родовое
// слово, во втором — различающий топоним.
func nameMatches(want, got string) bool {
	w := stripGenericWords(normalizeName(want))
	g := stripGenericWords(normalizeName(got))
	if w == "" || g == "" {
		return false
	}
	return w == g
}

// genericWords — слова, не различающие площадки: они одинаково стоят у десятка
// разных кинотеатров, поэтому при сверке имён отбрасываются.
var genericWords = map[string]bool{
	"кинотеатр": true, "киноцентр": true, "кино": true, "кинозал": true,
	"cinema": true, "cinemas": true, "кинокомплекс": true,
	"центр": true, "культурный": true, "летний": true, "москва": true,
	"городской": true, "московский": true, "детский": true, "государственный": true,
}

func stripGenericWords(s string) string {
	fields := strings.Fields(s)
	kept := make([]string, 0, len(fields))
	for _, f := range fields {
		if genericWords[f] {
			continue
		}
		kept = append(kept, f)
	}
	// Имя, состоящее из одних родовых слов («Летний кинотеатр»), опознанию не
	// поддаётся: таких объектов в городе несколько, и любой из них подставится.
	return strings.Join(kept, " ")
}

// queryPhoton возвращает первый ответ, прошедший гейт.
//
// wantName непустое — включается сверка имён (ступень поиска по названию).
// Пустое — ищем по адресу, и имя объекта в ответе ничего не значит.
func queryPhoton(c *Client, base, query, wantName string) (*GeoPoint, string, error) {
	u := photonURL(base, query)
	body, err := c.getText(u)
	if err != nil {
		return nil, u, err
	}
	var resp photonResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return nil, u, fmt.Errorf("разбор ответа Photon: %w", err)
	}
	for _, f := range resp.Features {
		if !photonPlausible(f) {
			continue
		}
		if wantName != "" && !nameMatches(wantName, f.Properties.Name) {
			continue
		}
		return &GeoPoint{
			Lat:      f.Geometry.Coordinates[1],
			Lon:      f.Geometry.Coordinates[0],
			Address:  photonAddress(f),
			Evidence: u,
		}, u, nil
	}
	return nil, u, nil
}

// photonAddress собирает адрес из полей ответа: он же уезжает в реестр, чтобы
// следующий прогон не решал ту же задачу заново.
func photonAddress(f photonFeature) string {
	parts := []string{}
	for _, p := range []string{f.Properties.City, f.Properties.Street, f.Properties.House} {
		if s := strings.TrimSpace(p); s != "" {
			parts = append(parts, s)
		}
	}
	return strings.Join(parts, ", ")
}

// queryNominatim возвращает первый московский ответ, прошедший гейт.
func queryNominatim(c *Client, base, query string) (*GeoPoint, string, error) {
	u := nominatimURL(base, query)
	body, err := c.getText(u)
	if err != nil {
		return nil, u, err
	}
	var places []nominatimPlace
	if err := json.Unmarshal([]byte(body), &places); err != nil {
		return nil, u, fmt.Errorf("разбор ответа Nominatim: %w", err)
	}
	for _, p := range places {
		if !nominatimPlausible(p) {
			continue
		}
		lat, lon, ok := p.point()
		if !ok {
			continue
		}
		return &GeoPoint{Lat: lat, Lon: lon, Address: p.DisplayName, Evidence: u}, u, nil
	}
	return nil, u, nil
}

// GeoTarget — что известно о площадке до геокодирования.
type GeoTarget struct {
	Name      string
	Address   string
	IsNetwork bool
}

// stepQuery — что подаётся на вход ступени. Пустая строка означает, что ступень
// для этой площадки неисполнима (например, в адресе нет торгового центра).
func stepQuery(step string, t GeoTarget) string {
	switch step {
	case stepPhotonRaw:
		return strings.TrimSpace(t.Address)
	case stepNominatimNorm:
		if strings.TrimSpace(t.Address) == "" {
			return ""
		}
		return normalizeAddress(t.Address)
	case stepPhotonMall:
		return extractMall(t.Address)
	case stepPhotonTitle:
		if strings.TrimSpace(t.Name) == "" {
			return ""
		}
		return titleQuery(t.Name)
	}
	return ""
}

// geocode проводит площадку по каскаду.
//
// Каскад останавливается на первом ответе, ПРОШЕДШЕМ ГЕЙТ: ответ, который гейт
// отбраковал, переводит на следующую ступень, а не завершает поиск.
func geocode(c *Client, t GeoTarget, photonAPI, nominatimAPI string) GeoOutcome {
	steps := planSteps(strings.TrimSpace(t.Address) != "", t.IsNetwork)
	out := GeoOutcome{}

	for _, step := range steps {
		q := stepQuery(step, t)
		if q == "" {
			continue
		}

		var pt *GeoPoint
		var evidence string
		var err error
		switch step {
		case stepNominatimNorm:
			pt, evidence, err = queryNominatim(c, nominatimAPI, q)
		case stepPhotonTitle:
			// Единственная ступень, где имя объекта в ответе обязано совпасть с
			// искомым: только здесь мы ищем площадку по названию.
			pt, evidence, err = queryPhoton(c, photonAPI, q, t.Name)
		default:
			pt, evidence, err = queryPhoton(c, photonAPI, q, "")
		}
		out.Evidence = evidence
		if err != nil {
			out.Errors = append(out.Errors, fmt.Sprintf("%s: %v", step, err))
			continue
		}
		if pt == nil {
			continue
		}
		pt.Step = step

		if !needsCrossCheck(step) {
			out.Point = pt
			return out
		}

		// Искали по названию — проверяем той же строкой у второго геокодера.
		check, checkURL, checkErr := queryNominatim(c, nominatimAPI, q)
		out.Evidence = checkURL
		if checkErr != nil {
			out.Errors = append(out.Errors, fmt.Sprintf("%s/кросс-чек: %v", step, checkErr))
		}
		if checkErr != nil || check == nil {
			// Проверка неприменима, а не расхождение: сравнивать не с чем.
			// Молчание Nominatim здесь норма — по сырой строке он берёт 1 адрес
			// из 12, и трактовка молчания как расхождения выбросила бы в
			// geo_unknown почти все одиночные площадки.
			pt.Evidence = evidence
			out.Point = pt
			out.Notes = append(out.Notes, noteGeoUnverified)
			return out
		}
		if haversineKm(pt.Lat, pt.Lon, check.Lat, check.Lon) > crossCheckLimitKm {
			// Два геокодера показывают разные места — верить нельзя ни одному.
			continue
		}
		pt.Evidence = evidence
		out.Point = pt
		return out
	}

	return out
}

// matchEnrichers сопоставляет строки реестра с площадками обогатителя.
//
// Сопоставление только по нормализованному названию и только когда кандидат
// единственный с обеих сторон: координат на этом шаге ещё нет, расстоянием
// отсеять нечем, а «ближайший по имени» из 135 объектов OSM даёт ровно ту тихую
// подмену, против которой строится весь гейт.
//
// Вторым значением возвращает ID строк с неразличимым именем: в московском
// листинге таких пар шесть («PRIME CINEMA», «Времена года», «Родина»,
// «Киноквартал», «Космик», «Колибри»).
//
// Возвращаются они независимо от того, нашёлся ли кандидат у обогатителя, и это
// не перестраховка: первый живой прогон выдал обеим строкам «PRIME CINEMA»
// ОДНУ И ТУ ЖЕ точку — не через обогатитель, а через ступень поиска по
// названию. Запрос-то у них одинаковый. Значит неразличимость имени закрывает
// площадке не только сопоставление, но и весь поиск по имени; иначе два разных
// зала сливаются в одну точку с видом полной достоверности.
func matchEnrichers(rows []EaisRow, venues []EnrichedVenue) (map[string]EnrichedVenue, []string) {
	rowsByName := map[string][]EaisRow{}
	for _, r := range rows {
		key := normalizeName(r.Company)
		if key == "" {
			continue
		}
		rowsByName[key] = append(rowsByName[key], r)
	}

	venuesByName := map[string][]EnrichedVenue{}
	for _, v := range venues {
		key := normalizeName(v.Name)
		if key == "" {
			continue
		}
		venuesByName[key] = append(venuesByName[key], v)
	}

	matched := map[string]EnrichedVenue{}
	var ambiguous []string

	for _, r := range rows {
		key := normalizeName(r.Company)
		if key == "" {
			continue
		}
		if len(rowsByName[key]) > 1 {
			// Две строки реестра с одним именем: какая из них чья, по названию
			// не решить — ни у обогатителя, ни в геокодере.
			ambiguous = append(ambiguous, r.ID)
			continue
		}
		cands := venuesByName[key]
		if len(cands) != 1 {
			continue
		}
		matched[r.ID] = cands[0]
	}

	return matched, ambiguous
}
