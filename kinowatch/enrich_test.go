package main

// Тесты обогатителей. Фикстуры — куски живых ответов, снятые 31.07.2026:
// справочник КАРО и Overpass. Сети в тестах нет.

import (
	"strings"
	"testing"
)

const karoFixture = `{"result":0,"data":{"theatre":[
{"id":3,"city_id":1,"name":"7 Атриум","address":"Москва, ул. Земляной вал, 33 (ТРК \"АТРИУМ\")","latitude":55.756931,"longitude":37.658909,"metro":["Курская","Чкаловская"]},
{"id":6,"city_id":1,"name":"5 Иридиум","address":"г. Зеленоград, Крюковская пл., 1, ж/д, ст. Крюково ТРЦ «Иридиум», 3 этаж","latitude":55.982533,"longitude":37.17503,"metro":[]},
{"id":9,"city_id":1,"name":"","address":"без имени","latitude":55.0,"longitude":37.0,"metro":[]}
]}}`

func TestParseKaro(t *testing.T) {
	venues, err := parseKaroDirectory(karoFixture)
	if err != nil {
		t.Fatalf("разбор справочника: %v", err)
	}

	// Безымянная площадка выброшена: сопоставлять её со строкой ЕАИС нечем.
	if len(venues) != 2 {
		t.Fatalf("площадок %d, ожидалось 2 (безымянная не считается): %+v", len(venues), venues)
	}

	atrium := venues[0]
	if atrium.Name != "7 Атриум" || atrium.Source != "karo" {
		t.Errorf("первая площадка разобрана как %+v", atrium)
	}
	if atrium.Lat != 55.756931 || atrium.Lon != 37.658909 {
		t.Errorf("координаты КАРО разобраны как %v, %v", atrium.Lat, atrium.Lon)
	}
	if !strings.Contains(atrium.Address, "Земляной вал") {
		t.Errorf("адрес потерян: %q", atrium.Address)
	}
}

// Зеленоград в справочнике КАРО есть, и это нормально: охват режется на уровне
// реестра (applyCityScope), а не второй раз здесь. Два правила охвата разошлись
// бы при первой же правке одного из них.
func TestParseKaroKeepsOutOfScopeVenues(t *testing.T) {
	venues, err := parseKaroDirectory(karoFixture)
	if err != nil {
		t.Fatalf("разбор справочника: %v", err)
	}

	found := false
	for _, v := range venues {
		if strings.Contains(v.Address, "Зеленоград") {
			found = true
		}
	}
	if !found {
		t.Error("зеленоградская площадка отфильтрована внутри парсера — охват решается не здесь")
	}
}

const overpassFixture = `{"version":0.6,"elements":[
{"type":"node","id":293426006,"lat":55.8632961,"lon":37.5465800,"tags":{"amenity":"cinema","level":"2","name":"Киномакс XL","website":"https://kinomax.ru/"}},
{"type":"node","id":430135770,"lat":55.8808411,"lon":37.4488558,"tags":{"alt_name":"РК Киносфера","amenity":"cinema","name":"Nescafe-IMAX"}},
{"type":"way","id":111,"center":{"lat":55.7,"lon":37.6},"tags":{"amenity":"cinema","name":"Художественный","addr:street":"Арбатская площадь","addr:housenumber":"14"}},
{"type":"node","id":222,"lat":55.5,"lon":37.5,"tags":{"amenity":"cinema"}}
]}`

func TestParseOverpass(t *testing.T) {
	venues, err := parseOverpass(overpassFixture)
	if err != nil {
		t.Fatalf("разбор Overpass: %v", err)
	}

	// Безымянный объект выброшен — из 135 объектов такие есть.
	if len(venues) != 3 {
		t.Fatalf("объектов %d, ожидалось 3 (безымянный не считается): %+v", len(venues), venues)
	}

	if venues[0].Website != "https://kinomax.ru/" {
		t.Errorf("сайт из тегов потерян: %+v", venues[0])
	}
}

// Контур здания (way) своих lat/lon не имеет — координаты приходят полем
// center. Без этой ветки часть площадок молча выпала бы из обогатителя.
func TestParseOverpassUsesCenterForWays(t *testing.T) {
	venues, err := parseOverpass(overpassFixture)
	if err != nil {
		t.Fatalf("разбор Overpass: %v", err)
	}

	var hudozh *EnrichedVenue
	for i := range venues {
		if venues[i].Name == "Художественный" {
			hudozh = &venues[i]
		}
	}
	if hudozh == nil {
		t.Fatal("объект-контур не попал в выдачу — потеряны координаты из center")
	}
	if hudozh.Lat != 55.7 || hudozh.Lon != 37.6 {
		t.Errorf("координаты контура разобраны как %v, %v", hudozh.Lat, hudozh.Lon)
	}
	if hudozh.Address != "Москва, Арбатская площадь, 14" {
		t.Errorf("адрес из тегов addr:* собран как %q", hudozh.Address)
	}
}

func TestOsmAddress(t *testing.T) {
	cases := []struct {
		tags map[string]string
		want string
	}{
		{map[string]string{"addr:street": "Арбатская площадь", "addr:housenumber": "14"}, "Москва, Арбатская площадь, 14"},
		{map[string]string{"addr:street": "Арбатская площадь"}, "Москва, Арбатская площадь"},
		{map[string]string{"addr:housenumber": "14"}, ""},
		{map[string]string{}, ""},
	}
	for _, c := range cases {
		if got := osmAddress(c.tags); got != c.want {
			t.Errorf("osmAddress(%v) = %q, want %q", c.tags, got, c.want)
		}
	}
}

// Адрес обогатителя — это вход геокодера, поэтому он обязан переживать
// нормализацию: у КАРО название ТЦ приходит в скобках, а не в кавычках.
func TestKaroAddressSurvivesNormalization(t *testing.T) {
	venues, err := parseKaroDirectory(karoFixture)
	if err != nil {
		t.Fatalf("разбор справочника: %v", err)
	}

	norm := normalizeAddress(venues[0].Address)
	if !strings.Contains(norm, "Земляной вал") {
		t.Errorf("улица потеряна при нормализации: %q", norm)
	}
	if !strings.HasPrefix(norm, "Москва,") {
		t.Errorf("нормализованный адрес обязан начинаться с города: %q", norm)
	}
}

// Ноль объектов при успешном ответе — отказ сервиса, а не «кинотеатров нет».
// Замерено живьём: два запроса подряд дали 0 и 123 объекта, и по первому вывод
// о полноте реестра был бы неверным.
func TestParseOverpassEmptyIsNotSilent(t *testing.T) {
	// Сам разбор пустого ответа ошибкой не считается — решение принимает
	// сетевая обёртка, у которой есть и тело, и код ответа.
	venues, err := parseOverpass(`{"version":0.6,"elements":[]}`)
	if err != nil {
		t.Fatalf("разбор пустого ответа: %v", err)
	}
	if len(venues) != 0 {
		t.Errorf("из пустого ответа получено %d площадок", len(venues))
	}
}
