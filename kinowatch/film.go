package main

// Каскад матчинга: как из позиции афиши понять, что за ней стоит искомый фильм.
//
// Задача нетривиальна потому, что фильм ищется не по названию. В РФ он идёт
// «предсеансовым обслуживанием» и в афише маскируется: позиция называется
// «Волшебник 6+», а внутри хронометраж 107 минут и чужой синопсис. Поиск по
// названию такие сеансы пропускает целиком.
//
// Каскад устроен уровнями от самого надёжного признака к самому шаткому, и
// КАЖДЫЙ уровень объясняет своё решение — код уровня уезжает в MatchedBy, а
// уверенность в Confidence. Без объяснения находка непроверяема: «нашли» и
// «угадали по длительности» выглядели бы одинаково.
//
// Отдельно важен исход «нечем было проверить». У КАРО хронометража нет вовсе,
// то есть два решающих уровня там неприменимы — и это не то же самое, что «не
// сошлось». Смешать их значило бы выдать непокрытость сети за отсутствие
// фильма.

import (
	"regexp"
	"strings"
)

// FilmProfile — то, что мы знаем об искомом фильме. Живёт в Craft подстраницей
// профиля и приходит бинарнику на stdin вместе с реестром.
//
// Wrappers — известные короткометражки-прикрытия. Обратного сопоставления
// «обёртка → фильм» они не дают: разведка показала, что одно «Прощание»
// прикрывает и «Одиссею», и «Историю игрушек 5». Поэтому обёртка отвечает на
// вопрос «эта позиция серая», а какой за ней фильм — решают уровни выше.
type FilmProfile struct {
	Title    string   `json:"title"`
	Aliases  []string `json:"aliases"`
	Patterns []string `json:"patterns"`
	Wrappers []string `json:"wrappers"`

	// Вилка хронометража искомого фильма. Ноль означает «не знаем» и выключает
	// уровни, на неё опирающиеся, — гадать по длительности без вилки нельзя.
	DurationMin int `json:"durationMin"`
	DurationMax int `json:"durationMax"`

	// NegativeTitles — фильмы, чья большая длительность законна (тот же
	// «Аватар»). Без них чистая аномалия хронометража ловила бы любое длинное
	// кино подряд.
	NegativeTitles []string `json:"negativeTitles"`

	// SynopsisHints — куски описания, характерные для искомого фильма. Только
	// бустер уверенности: сами по себе находкой не являются.
	SynopsisHints []string `json:"synopsisHints"`
}

// Коды уровней каскада. Пишутся в MatchedBy как есть — по ним видно, ЧЕМ
// доказана находка, а не только что она есть.
const (
	matchExact           = "exact"
	matchAlias           = "alias"
	matchPattern         = "pattern"
	matchWrapperSplit    = "wrapper-split"
	matchWrapperDuration = "wrapper+duration"
	matchDurationAnomaly = "duration-anomaly"

	// Исходы без находки. Различать их обязательно: первый означает «источник
	// дал всё, что нужно, и фильма тут нет», второй — «проверить было нечем».
	matchNone          = "none"
	matchNoneNoRuntime = "none:no-duration"
)

// Уверенность находки. Строкой, а не числом: в Craft это колонка, которую
// читает человек, и «low» там понятнее, чем 0.3.
const (
	confHigh   = "high"
	confMedium = "medium"
	confLow    = "low"
)

// Пометки находки — тем же словарным принципом, что и Note у площадки:
// фиксированный набор констант, иначе объединение множеств не дедуплицирует.
const (
	noteGreyRelease  = "film:grey-release"  // источник сам выдал признак серого проката
	noteSynopsisHit  = "film:synopsis-hit"  // описание совпало с профилем
	noteNoRuntime    = "film:no-duration"   // хронометража у источника нет вовсе
	noteWrapperKnown = "film:known-wrapper" // название совпало с известной обёрткой
)

// Match — решение каскада по одной позиции афиши.
//
// Unverifiable отвечает на вопрос «уровни про хронометраж вообще применялись?».
// У источника без длительности ответ «нет», и тогда отсутствие находки ничего
// не доказывает — площадка добирается вторым слоем, а не считается чистой.
type Match struct {
	Matched      bool
	By           string
	Confidence   string
	Unverifiable bool
	GreyRelease  bool
	Notes        []string
}

var (
	filmNoise = regexp.MustCompile(`[«»"'()\[\]{}.,:;!?\-–—_/\\]+`)
	// Возрастной рейтинг приклеивается к названию («Волшебник 6+») и к
	// сравнению отношения не имеет.
	ageRating = regexp.MustCompile(`\b\d{1,2}\s*\+`)
	// Технология показа тоже попадает в название у части источников.
	formatNoise = regexp.MustCompile(`(?i)\b(2d|3d|2д|3д|imax|4dx|atmos|dolby)\b`)
)

// normalizeFilmTitle приводит название позиции к сравнимому виду.
//
// Своя, а не normalizeName из registry.go: та снимает организационно-правовые
// префиксы («ООО», «ИП»), которые в названиях фильмов не встречаются, и не
// знает ни про возрастной рейтинг, ни про приклеенный формат показа.
func normalizeFilmTitle(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	s = strings.ReplaceAll(s, "ё", "е")
	s = ageRating.ReplaceAllString(s, " ")
	s = formatNoise.ReplaceAllString(s, " ")
	s = filmNoise.ReplaceAllString(s, " ")
	s = multiSpace.ReplaceAllString(s, " ")
	return strings.TrimSpace(s)
}

// splitMarkers — по чему рвётся склеенная позиция афиши.
//
// Живой пример из разведки (Алмаз Синема): «Одиссея*(предсеанс. обсл.) + м/ф
// "Историю не изменить" — Фильм демонстрируется в рамках аренды залов ИП
// Харчев М.А.». Настоящее название стоит первым куском, дальше идёт прикрытие.
var splitMarkers = []string{"предсеанс", "предс.обсл", "предсеансов", " + ", " & ", " и м/ф", "м/ф"}

// splitCut — позиция первого маркера склейки в названии, или -1.
// Отдельной функцией, потому что сам факт наличия маркера значим сам по себе:
// «предсеанс. обсл.» в тексте позиции — прямое заявление источника о серой
// схеме, независимо от того, удалось ли по кускам опознать фильм.
func splitCut(title string) int {
	low := strings.ToLower(title)
	cut := -1
	for _, m := range splitMarkers {
		if i := strings.Index(low, m); i >= 0 && (cut < 0 || i < cut) {
			cut = i
		}
	}
	return cut
}

// splitWrapper режет склеенную позицию на куски-кандидаты.
// Возвращает исходную строку одним куском, если ни один маркер не сработал.
func splitWrapper(title string) []string {
	cut := splitCut(title)
	if cut <= 0 {
		return []string{title}
	}

	parts := []string{strings.TrimSpace(title[:cut])}
	if rest := strings.TrimSpace(title[cut:]); rest != "" {
		parts = append(parts, rest)
	}
	return parts
}

// inRuntimeRange — попадает ли хронометраж в вилку профиля.
// Вилка не задана → false: гадать без неё нельзя, и это не «не сошлось», а
// «нечем проверять» (вызывающий разбирает это отдельно).
func (p FilmProfile) inRuntimeRange(minutes int) bool {
	if minutes <= 0 || p.DurationMin <= 0 || p.DurationMax <= 0 {
		return false
	}
	return minutes >= p.DurationMin && minutes <= p.DurationMax
}

func (p FilmProfile) hasRuntimeRange() bool {
	return p.DurationMin > 0 && p.DurationMax > 0
}

// titleMatches — совпадает ли кусок названия с профилем по имени.
// Возвращает код уровня («exact» / «alias» / «pattern») либо пустую строку.
func (p FilmProfile) titleMatches(title string) string {
	norm := normalizeFilmTitle(title)
	if norm == "" {
		return ""
	}

	if norm == normalizeFilmTitle(p.Title) {
		return matchExact
	}
	for _, a := range p.Aliases {
		if norm == normalizeFilmTitle(a) {
			return matchAlias
		}
	}
	for _, pat := range p.Patterns {
		re, err := regexp.Compile("(?i)" + pat)
		if err != nil {
			// Битая регулярка в профиле — это дефект данных, а не сеанса.
			// Молча пропускаем уровень: падать на чужом вводе нельзя.
			continue
		}
		if re.MatchString(title) {
			return matchPattern
		}
	}
	return ""
}

func (p FilmProfile) isKnownWrapper(title string) bool {
	norm := normalizeFilmTitle(title)
	for _, w := range p.Wrappers {
		if norm == normalizeFilmTitle(w) {
			return true
		}
	}
	return false
}

func (p FilmProfile) isNegative(title string) bool {
	norm := normalizeFilmTitle(title)
	for _, n := range p.NegativeTitles {
		if norm == normalizeFilmTitle(n) {
			return true
		}
	}
	return false
}

func (p FilmProfile) synopsisHit(synopsis string) bool {
	if synopsis == "" {
		return false
	}
	low := strings.ToLower(synopsis)
	for _, h := range p.SynopsisHints {
		if h != "" && strings.Contains(low, strings.ToLower(h)) {
			return true
		}
	}
	return false
}

// matchShowtime прогоняет одну позицию афиши по каскаду.
//
// Порядок уровней — от факта к догадке, и он значим: точное имя не должно
// уступать эвристике по длительности, а находка по одной лишь длительности
// обязана нести низкую уверенность, чтобы её было видно в отчёте.
func matchShowtime(s Showtime, p FilmProfile) Match {
	m := Match{By: matchNone, Confidence: confLow}

	// Факт источника, а не наш вывод: непустое фискальное название означает,
	// что позиция идёт по серой схеме. Само по себе оно фильм не опознаёт —
	// одна обёртка прикрывает несколько разных фильмов, — поэтому здесь только
	// поднимается флаг, а решение принимают уровни ниже.
	if strings.TrimSpace(s.FilmFiscal) != "" {
		m.GreyRelease = true
		m.Notes = append(m.Notes, noteGreyRelease)
	}

	// Маркер склейки в тексте позиции — тоже факт источника, а не наш вывод:
	// «предсеанс. обсл.» прямо в названии означает серую схему независимо от
	// того, опознаем ли мы за ней фильм.
	glued := splitCut(s.Film) > 0
	if glued {
		m.GreyRelease = true
	}

	// Уровни 1–3: имя как есть. У склеенной позиции пропускаются: регулярка
	// профиля матчит подстроку и объявила бы находку по целой строке уровнем
	// «pattern», спрятав тот факт, что название пришлось разбирать.
	if !glued {
		if by := p.titleMatches(s.Film); by != "" {
			m.Matched, m.By, m.Confidence = true, by, confHigh
			return p.finish(s, m)
		}
	}

	// Уровень 4: расщепление склеенной позиции. Настоящее название часто стоит
	// первым куском, а прикрытие приклеено следом.
	if parts := splitWrapper(s.Film); len(parts) > 1 {
		for _, part := range parts {
			if by := p.titleMatches(part); by != "" {
				m.Matched = true
				m.By = matchWrapperSplit + ":" + by
				// Уверенность как у имени: резали по маркеру самого источника,
				// а совпал уже отдельный кусок, а не строка целиком.
				m.Confidence = confHigh
				return p.finish(s, m)
			}
		}
	}

	// Уровень 5: известная обёртка ПЛЮС подходящий хронометраж. Это кейс
	// «Волшебник, 152 мин» — название чужое, длительность выдаёт подмену.
	if p.isKnownWrapper(s.Film) {
		m.Notes = append(m.Notes, noteWrapperKnown)
		if p.inRuntimeRange(s.DurationM) {
			m.Matched, m.By, m.Confidence = true, matchWrapperDuration, confMedium
			m.GreyRelease = true
			return p.finish(s, m)
		}
	}

	// Уровень 6: чистая аномалия хронометража. Самый шаткий уровень — только
	// низкая уверенность и только вне негативного списка.
	if p.inRuntimeRange(s.DurationM) && !p.isNegative(s.Film) {
		m.Matched, m.By, m.Confidence = true, matchDurationAnomaly, confLow
		return p.finish(s, m)
	}

	// Находки нет. Дальше важно, ПОЧЕМУ её нет.
	if s.DurationM <= 0 && p.hasRuntimeRange() {
		// Источник длительности не отдаёт вовсе (КАРО) — два решающих уровня
		// не применялись, и отсутствие находки ничего не доказывает.
		m.By = matchNoneNoRuntime
		m.Unverifiable = true
		m.Notes = append(m.Notes, noteNoRuntime)
	}
	return m
}

// finish — общий хвост найденной позиции: бустер по синопсису и понижение
// уверенности там, где проверить длительностью было нечем.
//
// Синопсис только двигает уверенность вверх и никогда не создаёт находку сам:
// описание у источников обрезано и повторяется у разных фильмов серии.
func (p FilmProfile) finish(s Showtime, m Match) Match {
	if p.synopsisHit(s.Synopsis) {
		m.Notes = append(m.Notes, noteSynopsisHit)
		if m.Confidence == confLow {
			m.Confidence = confMedium
		} else if m.Confidence == confMedium {
			m.Confidence = confHigh
		}
	}

	// Находка по одному лишь названию на источнике без хронометража (КАРО)
	// проверена слабее такой же находки у Киномакса, и это должно быть видно.
	if s.DurationM <= 0 && p.hasRuntimeRange() {
		m.Unverifiable = true
		m.Notes = append(m.Notes, noteNoRuntime)
		if m.Confidence == confHigh {
			m.Confidence = confMedium
		}
	}
	return m
}
