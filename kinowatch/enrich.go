package main

// Обогатители реестра: источники, у которых уже есть адрес площадки.
//
// ЕАИС даёт пять колонок и ни одного адреса, поэтому геокодеру нужен вход
// получше, чем голое название. Два источника с адресами проверены живьём:
// справочник сети КАРО (14 московских площадок с готовыми координатами) и OSM
// Overpass (135 объектов amenity=cinema, у части есть website).
//
// Ни один из них не считается истиной сам по себе: сопоставление со строкой
// ЕАИС идёт по нормализованному названию и только при единственном кандидате
// (matchEnrichers в geo.go). Лишний адрес хуже отсутствующего — он молча
// поставит площадке чужую точку.

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strings"
)

const (
	karoDirectoryURL = "https://api.karofilm.ru/directory/1"
	overpassURL      = "https://overpass-api.de/api/interpreter"
)

// overpassQuery — все кинотеатры в границах Москвы.
// Берём и точки, и контуры зданий: часть площадок размечена полигоном, у него
// координаты приходят отдельным полем center.
const overpassQuery = `[out:json][timeout:60];` +
	`area["name"="Москва"]["admin_level"="4"]->.a;` +
	`(node["amenity"="cinema"](area.a);way["amenity"="cinema"](area.a););` +
	`out center;`

type karoResponse struct {
	Result int `json:"result"`
	Data   struct {
		Theatre []struct {
			ID        int     `json:"id"`
			CityID    int     `json:"city_id"`
			Name      string  `json:"name"`
			Address   string  `json:"address"`
			Latitude  float64 `json:"latitude"`
			Longitude float64 `json:"longitude"`
		} `json:"theatre"`
	} `json:"data"`
}

// parseKaroDirectory разбирает справочник площадок КАРО.
//
// Внимание на охват: в справочнике лежат и площадки за пределами города —
// «5 Иридиум» стоит в Зеленограде. Отсев по городу здесь не делается: строка
// ЕАИС сопоставляется по названию, а Зеленоград отсекается раньше, на уровне
// охвата реестра. Фильтровать дважды значило бы завести второе правило охвата.
func parseKaroDirectory(body string) ([]EnrichedVenue, error) {
	var resp karoResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return nil, fmt.Errorf("разбор справочника КАРО: %w", err)
	}

	out := make([]EnrichedVenue, 0, len(resp.Data.Theatre))
	for _, t := range resp.Data.Theatre {
		name := strings.TrimSpace(t.Name)
		if name == "" {
			continue
		}
		out = append(out, EnrichedVenue{
			Name:    name,
			Address: strings.TrimSpace(t.Address),
			Lat:     t.Latitude,
			Lon:     t.Longitude,
			Source:  "karo",
		})
	}
	return out, nil
}

type overpassElement struct {
	Type   string  `json:"type"`
	ID     int64   `json:"id"`
	Lat    float64 `json:"lat"`
	Lon    float64 `json:"lon"`
	Center *struct {
		Lat float64 `json:"lat"`
		Lon float64 `json:"lon"`
	} `json:"center"`
	Tags map[string]string `json:"tags"`
}

type overpassResponse struct {
	Elements []overpassElement `json:"elements"`
}

// parseOverpass разбирает ответ Overpass.
//
// Объект без имени пропускается: сопоставлять его со строкой ЕАИС нечем, а
// «ближайший безымянный» — это ровно та тихая подмена, против которой строится
// гейт. Из 135 объектов часть именно такая.
func parseOverpass(body string) ([]EnrichedVenue, error) {
	var resp overpassResponse
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		return nil, fmt.Errorf("разбор ответа Overpass: %w", err)
	}

	out := make([]EnrichedVenue, 0, len(resp.Elements))
	for _, e := range resp.Elements {
		name := strings.TrimSpace(e.Tags["name"])
		if name == "" {
			continue
		}

		lat, lon := e.Lat, e.Lon
		if e.Center != nil {
			// Контур здания: собственных lat/lon у way нет.
			lat, lon = e.Center.Lat, e.Center.Lon
		}
		if lat == 0 && lon == 0 {
			continue
		}

		out = append(out, EnrichedVenue{
			Name:    name,
			Address: osmAddress(e.Tags),
			Lat:     lat,
			Lon:     lon,
			Source:  "osm",
			Website: osmWebsite(e.Tags),
		})
	}
	return out, nil
}

// osmAddress собирает адрес из тегов addr:*. Пустая строка — законный исход:
// у большинства объектов адресных тегов нет, и тогда полезны только координаты.
func osmAddress(tags map[string]string) string {
	street := strings.TrimSpace(tags["addr:street"])
	house := strings.TrimSpace(tags["addr:housenumber"])
	if street == "" {
		return ""
	}
	if house == "" {
		return "Москва, " + street
	}
	return "Москва, " + street + ", " + house
}

// fetchKaro и fetchOverpass — сетевые обёртки над чистыми парсерами.
// Отказ источника не фатален: обогатители улучшают вход геокодера, но реестр
// строится и без них — площадки просто уходят на ступень поиска по названию.
func fetchKaro(c *Client, endpoint string) ([]EnrichedVenue, error) {
	body, err := c.getText(endpoint)
	if err != nil {
		return nil, err
	}
	return parseKaroDirectory(body)
}

func fetchOverpass(c *Client, endpoint string) ([]EnrichedVenue, error) {
	body, err := c.getText(endpoint + "?data=" + url.QueryEscape(overpassQuery))
	if err != nil {
		return nil, err
	}
	return parseOverpass(body)
}

// osmWebsite — сайт площадки из тегов. Когда шаг поиска сайта появится, это его
// первая и самая дешёвая ступень; пока — просто сохранённый факт.
func osmWebsite(tags map[string]string) string {
	for _, key := range []string{"website", "contact:website", "url"} {
		if v := strings.TrimSpace(tags[key]); v != "" {
			return v
		}
	}
	return ""
}
