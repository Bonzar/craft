package main

// Тесты геокодера. Сети нет: проверяются чистые функции — планировщик ступеней,
// разбор адреса, гейт правдоподобия и сопоставление с обогатителями. Живые
// ответы Photon и Nominatim зафиксированы литералами в фикстурах ниже — они
// сняты запросами 31.07.2026.

import (
	"encoding/json"
	"math"
	"strings"
	"testing"
)

// Ступени — не список ради списка: от него зависит, получит ли площадка чужие
// координаты. Сетевая без адреса не геокодируется вовсе, потому что
// единственная доступная ей ступень — поиск по названию, а он путает филиалы.
func TestPlanSteps(t *testing.T) {
	cases := []struct {
		name       string
		hasAddress bool
		isNetwork  bool
		want       []string
	}{
		{
			"адрес есть, одиночка — полный каскад",
			true, false,
			[]string{stepPhotonRaw, stepNominatimNorm, stepPhotonMall, stepPhotonTitle},
		},
		{
			"адрес есть, сетевая — без поиска по названию",
			true, true,
			[]string{stepPhotonRaw, stepNominatimNorm, stepPhotonMall},
		},
		{
			"адреса нет, одиночка — только название",
			false, false,
			[]string{stepPhotonTitle},
		},
		{
			"адреса нет, сетевая — ни одной ступени",
			false, true,
			nil,
		},
	}

	for _, c := range cases {
		got := planSteps(c.hasAddress, c.isNetwork)
		if len(got) != len(c.want) {
			t.Errorf("%s: ступеней %v, ожидалось %v", c.name, got, c.want)
			continue
		}
		for i := range got {
			if got[i] != c.want[i] {
				t.Errorf("%s: ступень %d = %q, ожидалось %q", c.name, i, got[i], c.want[i])
			}
		}
	}
}

// Прямое следствие правила про филиалы: ступень поиска по названию не должна
// достаться ни одной сетевой площадке, с адресом или без.
func TestNetworkVenuesNeverGetTitleStep(t *testing.T) {
	for _, hasAddress := range []bool{true, false} {
		for _, step := range planSteps(hasAddress, true) {
			if step == stepPhotonTitle {
				t.Errorf("сетевая площадка (адрес=%v) получила ступень %q — она путает филиалы",
					hasAddress, step)
			}
		}
	}
}

// Кросс-чек стоит денег, поэтому он только там, где искали по названию.
func TestNeedsCrossCheck(t *testing.T) {
	byName := []string{stepPhotonMall, stepPhotonTitle}
	byAddress := []string{stepPhotonRaw, stepNominatimNorm}

	for _, s := range byName {
		if !needsCrossCheck(s) {
			t.Errorf("ступень %q искала по названию — её ответ обязан проверяться", s)
		}
	}
	for _, s := range byAddress {
		if needsCrossCheck(s) {
			t.Errorf("ступень %q искала по адресу — лишний кросс-чек", s)
		}
	}
}

func TestNormalizeAddress(t *testing.T) {
	cases := []struct{ in, want string }{
		{
			"ул. Профсоюзная, 61А, ТЦ «Калужский»",
			"Москва, ул. Профсоюзная, 61А",
		},
		{
			"Москва, Пресненская набережная, 2, ТРЦ Афимолл Сити",
			"Москва, Пресненская набережная, 2",
		},
		{
			"г. Москва, Ходынский бульвар, 4, корп. 2",
			"Москва, Ходынский бульвар, 4",
		},
		{
			"Кутузовский проспект, 57, стр. 1, этаж 3",
			"Москва, Кутузовский проспект, 57",
		},
		{
			"Николоямская улица, 1",
			"Москва, Николоямская улица, 1",
		},
	}
	for _, c := range cases {
		if got := normalizeAddress(c.in); got != c.want {
			t.Errorf("normalizeAddress(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// Литера дома — часть адреса, а не мусор: «61А» и «61» это разные дома, и
// снятие литеры превращает попадание в промах. Корпус и строение, наоборот,
// Nominatim только сбивают.
func TestNormalizeAddressKeepsHouseLetterButDropsBuildingParts(t *testing.T) {
	got := normalizeAddress("ул. Профсоюзная, 61А, корп. 3, ТЦ «Калужский»")

	if !strings.Contains(got, "61А") {
		t.Errorf("литера дома потеряна: %q", got)
	}
	if strings.Contains(got, "корп") {
		t.Errorf("корпус остался в адресе: %q", got)
	}
	if strings.Contains(got, "Калужский") {
		t.Errorf("название ТЦ осталось в адресе: %q", got)
	}
}

func TestExtractMall(t *testing.T) {
	cases := []struct{ in, want string }{
		{"ул. Профсоюзная, 61А, ТЦ «Калужский»", "Калужский, Москва"},
		{"Пресненская набережная, 2, ТРЦ Афимолл Сити", "Афимолл Сити, Москва"},
		{"Николоямская улица, 1", ""},
	}
	for _, c := range cases {
		if got := extractMall(c.in); got != c.want {
			t.Errorf("extractMall(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// Ступень исполняется только когда ей есть что подать на вход: у адреса без
// торгового центра ступень mall пропускается, а не запрашивается пустой строкой.
func TestStepQuerySkipsWhenNothingToAsk(t *testing.T) {
	noMall := GeoTarget{Name: "Иллюзион", Address: "Николоямская улица, 1"}
	if got := stepQuery(stepPhotonMall, noMall); got != "" {
		t.Errorf("в адресе нет ТЦ, но ступень mall собрала запрос %q", got)
	}

	noAddress := GeoTarget{Name: "Иллюзион"}
	if got := stepQuery(stepNominatimNorm, noAddress); got != "" {
		t.Errorf("адреса нет, но ступень norm собрала запрос %q", got)
	}
	if got := stepQuery(stepPhotonTitle, noAddress); got != "Иллюзион, Москва" {
		t.Errorf("ступень title собрала %q", got)
	}
}

// Ответ Photon по запросу «Кинотеатр Иллюзион, Москва», снят живьём 31.07.
// Внимание: объект — автобусная остановка (osm_key=highway), но Type=house.
// Это и есть причина, по которой гейт не может опираться на вид объекта, а
// совпадение проверяется кросс-чеком.
const photonIllusionFixture = `{"features":[{"type":"Feature","properties":{"osm_key":"highway","osm_value":"bus_stop","type":"house","name":"Кинотеатр «Иллюзион»","street":"Николоямская улица","district":"Таганский","city":"Москва","state":"Москва","country":"Россия","countrycode":"RU"},"geometry":{"type":"Point","coordinates":[37.6465376,55.7482702]}}]}`

func TestPhotonPlausibleAcceptsLiveAnswer(t *testing.T) {
	var resp photonResponse
	if err := json.Unmarshal([]byte(photonIllusionFixture), &resp); err != nil {
		t.Fatalf("фикстура Photon не разбирается: %v", err)
	}
	if len(resp.Features) != 1 {
		t.Fatalf("фич в фикстуре %d", len(resp.Features))
	}
	f := resp.Features[0]

	if !photonPlausible(f) {
		t.Error("живой московский ответ не прошёл гейт")
	}
	if lat := f.Geometry.Coordinates[1]; math.Abs(lat-55.7482702) > 1e-6 {
		t.Errorf("широта разобрана как %v — порядок координат в GeoJSON [lon, lat]", lat)
	}
}

// Photon переключается на английский от заголовка Accept-Language: ru-RU и
// отдаёт city="Moscow". Заголовок у гео-клиента снят, но гейт обязан пережить
// оба написания — иначе он висит на одной этой ниточке, а её обрыв выглядит как
// «геокодер ничего не находит» (ровно это и случилось на первом прогоне).
func TestGateAcceptsMoscowInBothLanguages(t *testing.T) {
	for _, c := range [][2]string{
		{"Москва", "Москва"},
		{"Moscow", "Moscow"},
		{"г. Москва", ""},
		{"", "Москва"},
	} {
		if !isMoscow(c[0], c[1]) {
			t.Errorf("Москва не опознана: city=%q state=%q", c[0], c[1])
		}
	}
	if isMoscow("Магадан", "Магаданская область") {
		t.Error("чужой город признан Москвой")
	}
}

// Гейт держит две вещи: город обязан быть Москвой (иначе «Радуга кино» уезжает
// в Магадан) и ответ обязан быть адресного уровня, а не «город целиком».
func TestPhotonGateRejects(t *testing.T) {
	moscowHouse := func() photonFeature {
		var resp photonResponse
		json.Unmarshal([]byte(photonIllusionFixture), &resp)
		return resp.Features[0]
	}

	other := moscowHouse()
	other.Properties.City = "Магадан"
	other.Properties.State = "Магаданская область"
	if photonPlausible(other) {
		t.Error("ответ из другого города прошёл гейт")
	}

	coarse := moscowHouse()
	coarse.Properties.Type = "city"
	if photonPlausible(coarse) {
		t.Error("ответ уровня города целиком прошёл гейт — это не адрес")
	}

	broken := moscowHouse()
	broken.Geometry.Coordinates = nil
	if photonPlausible(broken) {
		t.Error("ответ без координат прошёл гейт")
	}
}

// Photon отвечает не «то, что вы назвали, или ничего», а ближайшим похожим.
// По запросу «HIGHENDER CINEMA, Москва» первым приходит «Prime Cinema»:
// московский кинотеатр адресного уровня — гейт по городу и типу он проходит
// насквозь, а площадка чужая (проверено живьём 31.07). Ловит это только сверка
// имён, и без неё каскад раздавал бы соседние кинотеатры вместо «не знаю».
func TestNameMatchesGuardsAgainstNeighbourCinema(t *testing.T) {
	if nameMatches("HIGHENDER CINEMA", "Prime Cinema") {
		t.Error("чужой кинотеатр признан искомым — площадка получила бы ложные координаты")
	}

	// Названия одной площадки в ЕАИС и в OSM расходятся уточнениями.
	for _, c := range [][2]string{
		{"Иллюзион", "Иллюзион"},
		{"Кинотеатр Иллюзион", "Иллюзион"},
		{"Иллюзион", "Кинотеатр «Иллюзион»"},
		{"ЁЛКА", "Елка"},
	} {
		if !nameMatches(c[0], c[1]) {
			t.Errorf("одна и та же площадка не опознана: %q против %q", c[0], c[1])
		}
	}

	if nameMatches("Иллюзион", "") {
		t.Error("объект без имени признан совпадением")
	}
}

// Ответ Nominatim по адресу Котельнической набережной, снят живьём 31.07.
const nominatimFixture = `[{"lat":"55.7461359","lon":"37.6423702","addresstype":"building","place_rank":30,"display_name":"1/15 кА, Котельническая набережная, Таганский район, Москва, Центральный федеральный округ, 109240, Россия","address":{"road":"Котельническая набережная","suburb":"Таганский район","city":"Москва","state":"Москва","country":"Россия"}}]`

func TestNominatimGate(t *testing.T) {
	var places []nominatimPlace
	if err := json.Unmarshal([]byte(nominatimFixture), &places); err != nil {
		t.Fatalf("фикстура Nominatim не разбирается: %v", err)
	}
	p := places[0]

	if !nominatimPlausible(p) {
		t.Error("живой московский адрес не прошёл гейт")
	}
	lat, lon, ok := p.point()
	if !ok || math.Abs(lat-55.7461359) > 1e-6 || math.Abs(lon-37.6423702) > 1e-6 {
		t.Errorf("координаты разобраны как %v, %v (ok=%v)", lat, lon, ok)
	}

	coarse := p
	coarse.PlaceRank = 16 // уровень города
	if nominatimPlausible(coarse) {
		t.Error("ответ уровня города прошёл гейт")
	}

	elsewhere := p
	elsewhere.Address.City = "Магадан"
	elsewhere.Address.State = "Магаданская область"
	if nominatimPlausible(elsewhere) {
		t.Error("немосковский ответ прошёл гейт")
	}
}

// Порог кросс-чека имеет смысл, только если расстояние считается верно.
// Контрольная величина — тот самый промах по бренду: «Кронверк Вэйпарк» против
// «Кронверк Облака», около 35 км.
func TestHaversineKm(t *testing.T) {
	if d := haversineKm(55.75, 37.62, 55.75, 37.62); d != 0 {
		t.Errorf("расстояние до самой себя %v, ожидался ноль", d)
	}

	// Две точки Москвы примерно в километре друг от друга.
	near := haversineKm(55.7482, 37.6465, 55.7560, 37.6520)
	if near < 0.5 || near > 1.5 {
		t.Errorf("близкие точки дали %v км — ожидался порядок километра", near)
	}
	if near > crossCheckLimitKm {
		t.Errorf("соседние точки %v км превысили порог кросс-чека %v", near, crossCheckLimitKm)
	}

	// Разные филиалы одной сети на разных концах области.
	far := haversineKm(55.7482, 37.6465, 55.9000, 38.1000)
	if far < 25 || far > 45 {
		t.Errorf("разнесённые точки дали %v км — ожидался промах порядка 35 км", far)
	}
	if far <= crossCheckLimitKm {
		t.Errorf("промах в %v км не превысил порог %v — кросс-чек бесполезен", far, crossCheckLimitKm)
	}
}

// Сопоставление с обогатителем — место, где легче всего тихо подменить площадку.
// Правило: только единственный кандидат с обеих сторон.
func TestMatchEnrichers(t *testing.T) {
	rows := []EaisRow{
		{ID: "6038", Company: "Иллюзион"},
		{ID: "7001", Company: "PRIME CINEMA"},
		{ID: "7002", Company: "PRIME CINEMA"},
		{ID: "8000", Company: "Каро под звёздами"},
	}
	venues := []EnrichedVenue{
		{Name: "Иллюзион", Address: "Николоямская улица, 1", Source: "osm"},
		{Name: "Prime Cinema", Address: "где-то", Source: "osm"},
		{Name: "КАРО под звездами", Address: "ВДНХ", Source: "karo"},
	}

	matched, ambiguous := matchEnrichers(rows, venues)

	if v, ok := matched["6038"]; !ok || v.Address != "Николоямская улица, 1" {
		t.Errorf("однозначная площадка не сопоставлена: %+v (ok=%v)", v, ok)
	}

	// Ёфикация и регистр не должны мешать: normalizeName их снимает.
	if _, ok := matched["8000"]; !ok {
		t.Error("«Каро под звёздами» и «КАРО под звездами» — одна площадка, сопоставление не сошлось")
	}

	// Две строки с одним именем: отдать обеим один адрес значит поставить двум
	// залам одну точку.
	if _, ok := matched["7001"]; ok {
		t.Error("строка из неразличимой пары получила адрес обогатителя")
	}
	if _, ok := matched["7002"]; ok {
		t.Error("вторая строка неразличимой пары получила адрес обогатителя")
	}
	if len(ambiguous) != 2 {
		t.Errorf("в неоднозначных %d строк, ожидалось 2: %v", len(ambiguous), ambiguous)
	}
}

// Неразличимость имени закрывает площадке не только сопоставление с
// обогатителем, но и поиск по имени вообще.
//
// Дефект, вскрытый первым живым прогоном: обеим строкам «PRIME CINEMA» ступень
// photon-title выдала ОДНУ И ТУ ЖЕ точку — запрос-то у них одинаковый. Пометка
// про дубль при этом стояла, а координаты выглядели достоверными. Поэтому пара
// объявляется неоднозначной независимо от того, есть ли кандидат у обогатителя.
func TestNameDuplicatesReportedEvenWithoutEnricherCandidate(t *testing.T) {
	rows := []EaisRow{
		{ID: "7001", Company: "PRIME CINEMA"},
		{ID: "7002", Company: "PRIME CINEMA"},
		{ID: "6038", Company: "Иллюзион"},
	}
	// Обогатитель пуст: ни одного кандидата ни для кого.
	_, ambiguous := matchEnrichers(rows, nil)

	if len(ambiguous) != 2 {
		t.Fatalf("неоднозначных %d, ожидалось 2 — иначе обе строки уйдут искать себя по одинаковому имени: %v",
			len(ambiguous), ambiguous)
	}
	for _, id := range ambiguous {
		if id == "6038" {
			t.Error("уникальная площадка помечена неоднозначной")
		}
	}
}

// Обратная сторона: если у обогатителя два объекта с одним именем, не
// сопоставляется никто — расстоянием отсеять нечем, координат ещё нет.
func TestMatchEnrichersRejectsAmbiguousVenue(t *testing.T) {
	rows := []EaisRow{{ID: "9000", Company: "Космик"}}
	venues := []EnrichedVenue{
		{Name: "Космик", Address: "адрес один", Source: "osm"},
		{Name: "Космик", Address: "адрес два", Source: "osm"},
	}

	matched, _ := matchEnrichers(rows, venues)
	if v, ok := matched["9000"]; ok {
		t.Errorf("сопоставлено с одним из двух одноимённых объектов: %+v", v)
	}
}

func TestGeoURLs(t *testing.T) {
	u := photonURL("https://photon.komoot.io/api/", "Иллюзион, Москва")
	if !strings.Contains(u, "q=") || !strings.Contains(u, "limit=3") {
		t.Errorf("URL Photon собран неверно: %q", u)
	}
	// lang=ru не поддерживается Photon и выглядит как «ничего не найдено».
	if strings.Contains(u, "lang=") {
		t.Errorf("в URL Photon просочился lang: %q", u)
	}

	n := nominatimURL("https://nominatim.openstreetmap.org/search", "Москва, Николоямская улица, 1")
	for _, want := range []string{"format=jsonv2", "addressdetails=1", "limit=3"} {
		if !strings.Contains(n, want) {
			t.Errorf("в URL Nominatim нет %q: %s", want, n)
		}
	}
}
