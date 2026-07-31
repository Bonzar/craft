package main

// Выход бинарника: наблюдения по сеансам и правила их сведения.
//
// Прогоны идут в изолированных контейнерах и могут пересекаться, поэтому
// наивный upsert здесь ломается двумя способами. Разные прогоны видят разное —
// один нашёл сеанс, другой через минуту получил пустой ответ, и запись «кто
// последний, тот и прав» стёрла бы находку. И гонка на создании — два прогона,
// не увидевшие элемента, оба создадут его. Оба случая закрываются правилами
// ниже: слиянием по доказательной силе и дедупом по паре «ключ + SourceID».

import (
	"crypto/sha256"
	"encoding/hex"
	"strconv"
	"strings"
)

// Ключи свойств коллекции showtimes — в схлопнутом виде, как и у cinemas:
// display-имя без разделителей в нижнем регистре.
const (
	sKey          = "key"
	sFilm         = "film"
	sCinema       = "cinema"
	sStartsAt     = "startsat"
	sHall         = "hall"
	sFormat       = "format"
	sPriceMin     = "pricemin"
	sPriceMax     = "pricemax"
	sOnSale       = "onsale"
	sConfidence   = "confidence"
	sMatchedBy    = "matchedby"
	sSource       = "source"
	sSeenOnYandex = "seenonyandex"
	sFirstSeen    = "firstseen"
	sLastSeen     = "lastseen"
	sDeepLink     = "deeplink"
	sSourceID     = "sourceid"
	sFilmFiscal   = "filmfiscal"
	sNote         = "note"
)

// ShowtimeObservation — один найденный сеанс, готовый к upsert.
//
// Key и SourceID вместе образуют идентичность элемента: рутина ищет
// существующий именно по паре, и по ней же чистит дубли.
type ShowtimeObservation struct {
	Key      string            `json:"key"`
	SourceID string            `json:"sourceId"`
	Fields   map[string]string `json:"fields"`
}

// showtimeKey — ключ идемпотентности сеанса.
//
// Фильм в ключе обязателен: без него два разных сеанса в 19:00 в одном
// кинотеатре схлопнулись бы в один ключ, и дедуп удалил бы живую находку.
// Фильм кладётся НОРМАЛИЗОВАННЫЙ — одно и то же кино приезжает как «Одиссея» и
// «Odyssey», и сырая строка развела бы их по разным ключам.
//
// Зал ключ не спасает и не ломает: у источников без номера зала (СИНЕМА ПАРК,
// Mori) он пуст, ключ вырождается в тройку, и различает такие сеансы уже
// SourceID.
func showtimeKey(cinema, startsAt, film, hall string) string {
	return strings.Join([]string{
		strings.TrimSpace(cinema),
		strings.TrimSpace(startsAt),
		normalizeFilmTitle(film),
		strings.TrimSpace(hall),
	}, "|")
}

// showtimeFingerprint — подставной идентификатор для источников, которые своего
// не дают.
//
// Хеш берётся только от НЕИЗМЕННЫХ свойств сеанса: площадка, фильм, время,
// зал, формат. Цена и признак продажи в него не входят намеренно — они
// меняются у живого сеанса, и как раз их изменение есть то, ради чего
// инструмент работает. Возьми я их в хеш, тот же сеанс после открытия продаж
// дал бы новый отпечаток, upsert промахнулся бы, завёлся бы второй элемент со
// свежим FirstSeen — и момент открытия продаж выглядел бы новым сеансом, а не
// изменением старого. То есть ровно в целевой момент инструмент врал бы громче
// всего.
//
// Отсюда требование к адаптерам: поля, входящие в отпечаток, не выдумываются и
// не нормализуются «для красоты» — любая вольность в них создаёт ложную новинку.
func showtimeFingerprint(cinema string, s Showtime) string {
	raw := strings.Join([]string{
		strings.TrimSpace(cinema),
		normalizeFilmTitle(s.Film),
		strings.TrimSpace(s.StartsAt),
		strings.TrimSpace(s.Hall),
		strings.TrimSpace(s.Format),
	}, "|")
	sum := sha256.Sum256([]byte(raw))
	return "fp:" + hex.EncodeToString(sum[:8])
}

// buildShowtimeObservation собирает наблюдение по найденному сеансу.
//
// Пустого SourceID на выходе не бывает: где источник идентификатора не даёт,
// встаёт отпечаток. Иначе правило дедупа «оба пусты → схлопываем» удаляло бы
// ровно те беззальные сеансы, ради которых оно вводилось.
func buildShowtimeObservation(cinema string, s Showtime, m Match, source, now string) ShowtimeObservation {
	id := strings.TrimSpace(s.SourceID)
	if id != "" {
		// Идентификаторы разных источников не сравниваются между собой,
		// поэтому происхождение остаётся в самом значении.
		id = source + ":" + id
	} else {
		id = showtimeFingerprint(cinema, s)
	}

	fields := map[string]string{
		sKey:       showtimeKey(cinema, s.StartsAt, s.Film, s.Hall),
		sSourceID:  id,
		sCinema:    cinema,
		sFilm:      s.Film,
		sStartsAt:  s.StartsAt,
		sOnSale:    strconv.FormatBool(s.OnSale),
		sSource:    source,
		sFirstSeen: now,
		sLastSeen:  now,
	}

	// Пустое поле остаётся пустым: выдуманное значение хуже отсутствующего,
	// потому что участвует в ключе и в отпечатке.
	setIfNotEmpty(fields, sHall, s.Hall)
	setIfNotEmpty(fields, sFormat, s.Format)
	setIfNotEmpty(fields, sFilmFiscal, s.FilmFiscal)
	setIfNotEmpty(fields, sDeepLink, s.DeepLink)
	setIfNotEmpty(fields, sMatchedBy, m.By)
	setIfNotEmpty(fields, sConfidence, m.Confidence)
	if s.PriceMin > 0 {
		fields[sPriceMin] = strconv.Itoa(s.PriceMin)
	}
	if s.PriceMax > 0 {
		fields[sPriceMax] = strconv.Itoa(s.PriceMax)
	}
	if len(m.Notes) > 0 {
		addNote(fields, m.Notes...)
	}

	return ShowtimeObservation{Key: fields[sKey], SourceID: id, Fields: fields}
}

func setIfNotEmpty(fields map[string]string, key, value string) {
	if v := strings.TrimSpace(value); v != "" {
		fields[key] = v
	}
}

// confidenceRank — доказательная сила уверенности для слияния.
func confidenceRank(c string) int {
	switch c {
	case confHigh:
		return 3
	case confMedium:
		return 2
	case confLow:
		return 1
	default:
		return 0
	}
}

// mergeShowtimes сводит существующий элемент с новым наблюдением.
//
// Побеждает не последний по времени, а сильнейший по доказательствам: находка
// не перетирается пустотой, уверенность не понижается молча. Исключение —
// цена и признак продажи: они у живого сеанса меняются, и новое наблюдение по
// ним всегда свежее старого. Ровно ради этих двух полей инструмент и работает.
func mergeShowtimes(existing, incoming map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range existing {
		out[k] = v
	}

	// FirstSeen — всегда старейший: по нему считается «что нового», и сдвиг
	// назад превратил бы вчерашнюю находку в сегодняшнюю новинку.
	if f := existing[sFirstSeen]; f == "" || (incoming[sFirstSeen] != "" && incoming[sFirstSeen] < f) {
		out[sFirstSeen] = incoming[sFirstSeen]
	}
	// LastSeen — всегда новейший.
	if incoming[sLastSeen] > existing[sLastSeen] {
		out[sLastSeen] = incoming[sLastSeen]
	}

	// Живые поля сеанса: берём свежие, даже если они «слабее».
	for _, k := range []string{sOnSale, sPriceMin, sPriceMax} {
		if v, ok := incoming[k]; ok {
			out[k] = v
		}
	}

	// Уверенность и её объяснение двигаются только вверх — вместе, чтобы
	// MatchedBy всегда соответствовал показанной уверенности.
	if confidenceRank(incoming[sConfidence]) > confidenceRank(existing[sConfidence]) {
		out[sConfidence] = incoming[sConfidence]
		if incoming[sMatchedBy] != "" {
			out[sMatchedBy] = incoming[sMatchedBy]
		}
	}

	// Остальное дозаполняется, но не затирается: пустота доказательной силы
	// не имеет.
	for k, v := range incoming {
		switch k {
		case sFirstSeen, sLastSeen, sOnSale, sPriceMin, sPriceMax, sConfidence, sMatchedBy, sNote:
			continue
		}
		if strings.TrimSpace(v) != "" && strings.TrimSpace(out[k]) == "" {
			out[k] = v
		}
	}

	// Note накапливает: пометки разных шагов и разных прогонов объединяются
	// множествами, иначе первая же ежечасная запись затёрла бы вчерашнюю.
	if n := incoming[sNote]; n != "" {
		addNote(out, strings.Split(n, ";")...)
	}

	return out
}

// attachHall доопределяет зал у беззального элемента по наблюдению второго слоя.
//
// Ключ и SourceID при этом НЕ пересчитываются, и это принципиально. Свой канал
// площадки зала не отдаёт и в следующем часе снова вычислит прежние ключ и
// отпечаток; пересчитай мы их здесь — upsert промахнулся бы, элемент заводился
// бы заново каждый час, и ежечасно рождалась бы ложная «новинка». То есть тот
// же отказ, ради защиты от которого цена исключена из отпечатка, просто зашедший
// с другой стороны. Зал ложится только в свою колонку — как факт для человека,
// а не как часть идентичности элемента.
func attachHall(fields map[string]string, hall string) {
	if h := strings.TrimSpace(hall); h != "" && strings.TrimSpace(fields[sHall]) == "" {
		fields[sHall] = h
	}
}

// matchByTriple ищет, к какому существующему элементу отнести наблюдение, у
// которого зал есть, а у элементов первого слоя его нет.
//
// Поиск идёт ТОЛЬКО среди беззальных: без этого ограничения наблюдение с залом
// 2 нашло бы сеанс в зале 1 и схлопнуло два реальных сеанса премьеры в один.
// Кандидат ровно один — это тот же сеанс, зал доопределяется. Кандидатов двое и
// больше — слияния нет: схлопнуть наугад значит потерять реальный сеанс, а это
// ровно тот отказ, против которого инструмент и делается.
func matchByTriple(candidates []ShowtimeObservation, cinema, startsAt, film string) *ShowtimeObservation {
	want := showtimeKey(cinema, startsAt, film, "")

	var found *ShowtimeObservation
	for i := range candidates {
		if strings.TrimSpace(candidates[i].Fields[sHall]) != "" {
			continue
		}
		if candidates[i].Key != want {
			continue
		}
		if found != nil {
			return nil
		}
		found = &candidates[i]
	}
	return found
}

// DedupResult — итог чистки дублей.
//
// Collapsed считает пары, неразличимые В ПРИНЦИПЕ: источник не дал ни зала, ни
// идентификатора, и два реальных сеанса одного фильма в один час в одном
// формате отличить нечем. Это честное ограничение источника, а не дефект
// алгоритма, и оно не прячется — число уходит в runs отдельной величиной,
// чтобы было видно, сколько правды мы недосчитались на конкретном источнике.
type DedupResult struct {
	Kept      []ShowtimeObservation `json:"kept"`
	Removed   int                   `json:"removed"`
	Collapsed int                   `json:"collapsed"`
}

// dedupShowtimes чистит повторы, оставляя старейший по FirstSeen.
//
// Дублем считается совпадение ключа И SourceID одновременно. Только ключа тут
// мало: у беззальных источников два реальных сеанса одного фильма в один час
// дают одинаковый ключ, и проход «по ключу» убил бы живую находку.
//
// Идентификаторы сравниваются только внутри одного источника — у разных
// источников нумерация своя, поэтому происхождение вшито в само значение
// SourceID при сборке наблюдения.
func dedupShowtimes(items []ShowtimeObservation) DedupResult {
	seen := map[string]int{} // «ключ+id» → индекс в kept
	res := DedupResult{}

	for _, it := range items {
		id := it.Key + "\x00" + it.SourceID
		idx, ok := seen[id]
		if !ok {
			seen[id] = len(res.Kept)
			res.Kept = append(res.Kept, it)
			continue
		}

		res.Removed++
		if strings.HasPrefix(it.SourceID, "fp:") {
			// Отпечаток совпал целиком — значит у источника не было ни зала,
			// ни своего идентификатора, и два сеанса неразличимы в принципе.
			res.Collapsed++
		}

		res.Kept[idx].Fields = mergeShowtimes(res.Kept[idx].Fields, it.Fields)
	}

	return res
}
