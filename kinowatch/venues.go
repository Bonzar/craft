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

// moskinoLink — ссылка на страницу площадки в списке кинотеатров сети.
var moskinoLink = regexp.MustCompile(`href="/cinema/([^"/]+)/"`)

// parseMoskinoVenues разбирает список площадок Москино.
//
// Названий на странице списка нет — только слаги, а слаг и есть идентификатор
// в канале. Имя берётся из него же: «moskino_kosmos» → «moskino kosmos», чего
// достаточно для сопоставления по нормализованному названию.
func parseMoskinoVenues(body string) ([]NetworkVenue, error) {
	seen := map[string]bool{}
	var out []NetworkVenue
	for _, m := range moskinoLink.FindAllStringSubmatch(body, -1) {
		slug := m[1]
		if slug == "" || seen[slug] {
			continue
		}
		seen[slug] = true
		out = append(out, NetworkVenue{
			ID:   slug,
			Name: strings.NewReplacer("_", " ", "-", " ").Replace(slug),
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
			res.Unmatched = append(res.Unmatched, obs[i].Name)
			obs[i].Fields[fStatusClass] = pickClass(obs[i].Fields[fStatusClass], classNoSource)
			obs[i].Fields[fLastError] = "площадки нет в справочнике собственной сети"
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
func venueKey(name string) string {
	s := normalizeName(name)
	for _, brand := range []string{"каро под звездами", "каро", "киномакс", "москино", "синема парк", "формула кино", "синема стар", "mori cinema", "пять звезд"} {
		s = strings.TrimSpace(strings.TrimPrefix(s, brand))
	}
	s = venueRank.ReplaceAllString(s, "")
	// Хвост с городом: Киномакс зовёт площадку «Киномакс-Водный Москва», а
	// реестр — «Киномакс Водный». Без снятия хвоста не совпала бы ни одна.
	s = strings.TrimSpace(strings.TrimSuffix(strings.TrimSpace(s), "москва"))
	return strings.TrimSpace(s)
}
