package main

// Реестр кинотеатров: скелет берётся из ЕАИС Фонда кино.
//
// Почему именно ЕАИС, а не афиша или карты: регистрация демонстратора там
// обязательна по закону (ФЗ-126 + ПП РФ №837), поэтому этот перечень задаёт
// определение полноты. Афиши и карты видят только тех, кто им интересен.
//
// Что источник НЕ даёт: адреса, координат, ИНН и сайта. Пять колонок и всё.
// Поэтому адрес и сайт добываются отдельно (geo.go, site.go), а сопоставление
// строится на названии с проверкой города — иначе «Радуга кино» уезжает в
// Магадан, а «Дружба» на Камчатку (обе коллизии реальны, проверено).

import (
	"fmt"
	"regexp"
	"strings"
)

// EaisRow — строка листинга ЕАИС как она есть, без интерпретации.
type EaisRow struct {
	Region  string `json:"region"`
	City    string `json:"city"`
	ID      string `json:"eaisId"`
	Company string `json:"company"`
	Network string `json:"network"`
}

// moscowRegionCode — код Москвы в путях листинга ЕАИС.
const moscowRegionCode = "7700000000000"

// eaisCell вытаскивает ячейки таблицы по порядку следования в разметке.
// Разметка ЕАИС — не <table>, а <div class="d-tbl-item _region|_city|_id|_company|_cinema">,
// причём шапка (<div class="d-tbl-head">) состоит из ТЕХ ЖЕ ячеек. Наивная
// группировка по пять даёт лишнюю строку на каждую страницу (180 вместо 176),
// поэтому шапка отсекается по значению первой ячейки.
var eaisCell = regexp.MustCompile(`<div class="d-tbl-item _(region|city|id|company|cinema)"[^>]*>(.*?)</div>`)

// eaisPageURL — URL страницы листинга по региону.
// Внимание: форма ?region=<code> НЕ работает (301 на http и дальше 503),
// рабочая форма — код в пути. Первая страница и последующие устроены по-разному.
func eaisPageURL(base, region string, page int) string {
	base = strings.TrimRight(base, "/")
	if page <= 1 {
		return fmt.Sprintf("%s/demonstrators/%s/", base, region)
	}
	return fmt.Sprintf("%s/demonstrators/page/%d/%s/", base, page, region)
}

// parseEaisPage разбирает одну страницу листинга.
// IO-free: на вход сырой HTML, на выход строки. Именно поэтому тестируется
// на зафиксированной фикстуре, без сети.
func parseEaisPage(html string) []EaisRow {
	matches := eaisCell.FindAllStringSubmatch(html, -1)

	var rows []EaisRow
	var cur EaisRow
	var filled int

	for _, m := range matches {
		field, value := m[1], cleanCell(m[2])
		switch field {
		case "region":
			// Начало новой строки: если предыдущая недобрана, она битая — бросаем.
			cur = EaisRow{Region: value}
			filled = 1
		case "city":
			cur.City = value
			filled++
		case "id":
			cur.ID = value
			filled++
		case "company":
			cur.Company = value
			filled++
		case "cinema":
			cur.Network = value
			filled++
			if filled == 5 && !isHeaderRow(cur) {
				rows = append(rows, cur)
			}
			filled = 0
		}
	}
	return rows
}

// isHeaderRow опознаёт шапку таблицы. Она размечена теми же ячейками, что и
// данные, отличается только содержимым.
func isHeaderRow(r EaisRow) bool {
	return r.Region == "Регион" || r.City == "Город" || r.ID == "ID"
}

// cleanCell снимает вложенные теги и схлопывает пробелы.
var tagRe = regexp.MustCompile(`<[^>]*>`)

func cleanCell(s string) string {
	s = tagRe.ReplaceAllString(s, "")
	s = strings.ReplaceAll(s, "&nbsp;", " ")
	s = strings.ReplaceAll(s, "&amp;", "&")
	s = strings.ReplaceAll(s, "&quot;", `"`)
	return strings.TrimSpace(s)
}

// Города региона «Москва г», которые к городу Москве не относятся.
// Зеленоград административно Москва, но лежит далеко за МКАД; Московский и
// Мамыри — ТиНАО. Все три вне охвата по решению «строго внутри МКАД».
var outOfScopeCities = map[string]string{
	"Зеленоград г": "zelenograd",
	"Московский г": "tinao",
	"Мамыри д":     "tinao",
}

// ScopeDecision — почему строка попала в охват или не попала.
type ScopeDecision struct {
	Row     EaisRow `json:"row"`
	InScope bool    `json:"inScope"`
	Reason  string  `json:"reason,omitempty"`
}

// applyCityScope режет строки по городу — единственное, что можно решить
// прямо из ЕАИС, без адреса. Всё остальное (МКАД, дубли) решается позже,
// когда появятся адреса.
func applyCityScope(rows []EaisRow) []ScopeDecision {
	out := make([]ScopeDecision, 0, len(rows))
	for _, r := range rows {
		if reason, bad := outOfScopeCities[r.City]; bad {
			out = append(out, ScopeDecision{Row: r, InScope: false, Reason: reason})
			continue
		}
		out = append(out, ScopeDecision{Row: r, InScope: true})
	}
	return out
}

// cloneNetworks — сети реестра, за которыми стоит тот же оператор и тот же
// набор залов, что и за ведущей. Ключ — нормализованное название сети-клона,
// значение — название ведущей.
//
// Пока пара одна, и она предъявлена фактами разведки, а не выведена по
// похожести имён: «Киномакс», «Созвездие» и «Кинообслуживание» — три записи
// ЕАИС на один набор из семи залов. Наборы торговых центров совпадают по
// адресам, своих доменов у двух последних нет вовсе, расписание всех семи
// живёт на api.kinomax.ru, а ID идут поколениями (разрозненные у Киномакса,
// сплошной блок 9445–9494 у Кинообслуживания, 10768–10786 у Созвездия).
//
// Карта именно ручная. Схлопывать автоматически по похожести названий нельзя
// ровно по той же причине, по которой нельзя геокодировать неразличимые пары:
// «Родина» и «Родина» — это может быть и задвоенная регистрация, и два разных
// зала, и ошибка здесь молча стирает кинотеатр из знаменателя.
var cloneNetworks = map[string]string{
	"кинообслуживание": `АО "Киномакс" в г. Москва`,
	"созвездие":        `АО "Киномакс" в г. Москва`,
}

// venuesWithoutScreenings — площадки, у которых сущности «сеанс» нет вовсе.
//
// Это НЕ наша неготовность, а свойство самой площадки, поэтому класс
// объективный (no_online_sale) и выводит строку из знаменателя покрытия с явной
// причиной. Разница с uncovered принципиальна: uncovered означал бы, что
// адаптер не написан, и площадка вечно висела бы недоработкой.
//
// Установлено разведкой 03.08, а не предположено:
//   - Коперто: ресторан с приватным залом на 11 человек. Времена на сайте —
//     часы работы (11:00–23:00), билетов нет, только бронь столика.
//   - Эльдар: в афише 49 событий, из них 10 концертов и 6 спектаклей.
//     Кинопоказов нет ни одного — площадка работает как концертная.
//
// Обе остаются видимыми строками реестра: месячная перепроверка терминальных
// классов вернёт их, если кинопоказы появятся.
var venuesWithoutScreenings = map[string]string{
	"коперто": "приватный зал по брони, сеансов и билетов нет",
	"эльдар":  "афиша из концертов и спектаклей, кинопоказов нет",
}

// screeningsAbsentReason возвращает причину отсутствия сеансов, либо пустую
// строку. Сравнение по нормализованному названию — тем же способом, что у
// клонов.
func screeningsAbsentReason(company string) string {
	norm := normalizeName(company)
	for key, reason := range venuesWithoutScreenings {
		if strings.Contains(norm, key) {
			return reason
		}
	}
	return ""
}

// cloneLeader возвращает ведущую сеть для клона, либо пустую строку.
//
// Опрашивать клонов порознь нельзя: один физический сеанс записался бы трижды
// с тремя разными ключами, и дедуп их не схлопнул бы — ключи честно разные.
func cloneLeader(network string) string {
	return cloneNetworks[normalizeName(network)]
}

// DuplicateHint — кандидат в дубли по названию.
//
// НЕ схлопывается автоматически. По названию невозможно отличить задвоенную
// регистрацию одной площадки от двух разных залов: в московском листинге таких
// пар шесть — «PRIME CINEMA», «Времена года», «Родина», «Киноквартал»,
// «Космик», «Колибри». Каждая решается только адресом.
//
// Обратный случай важен не меньше: площадки одной сети, различимые по названию
// («Pushka», «Pushka Mitino», «Pushka Brateevo»), дублями НЕ считаются и в
// подсказки не попадают — схлопывание по бренду стёрло бы реальные кинотеатры
// из знаменателя покрытия.
type DuplicateHint struct {
	Normalized string   `json:"normalized"`
	IDs        []string `json:"eaisIds"`
}

// findDuplicateHints группирует строки по нормализованному названию.
func findDuplicateHints(rows []EaisRow) []DuplicateHint {
	byName := map[string][]string{}
	order := []string{}
	for _, r := range rows {
		key := normalizeName(r.Company)
		if key == "" {
			continue
		}
		if _, seen := byName[key]; !seen {
			order = append(order, key)
		}
		byName[key] = append(byName[key], r.ID)
	}

	var hints []DuplicateHint
	for _, key := range order {
		if len(byName[key]) > 1 {
			hints = append(hints, DuplicateHint{Normalized: key, IDs: byName[key]})
		}
	}
	return hints
}

var nameNoise = regexp.MustCompile(`[«»"'()\[\].,\-–—]+`)
var multiSpace = regexp.MustCompile(`\s+`)

// normalizeName приводит название к сравнимому виду.
// Учитывает, что в колонке «Организация» лежит обычно название площадки, но
// иногда настоящее юрлицо («ИП Харчев М.А.», «ОАО Планетарий») — префиксы
// организационных форм снимаются, иначе они шумят при сравнении.
func normalizeName(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	s = strings.ReplaceAll(s, "ё", "е")
	s = nameNoise.ReplaceAllString(s, " ")
	for _, p := range []string{"ооо ", "оао ", "зао ", "ао ", "ип ", "нп ", "гаук ", "гбук ", "фгбук ", "мбук ", "гау ", "гбу "} {
		s = strings.TrimPrefix(s, p)
	}
	s = multiSpace.ReplaceAllString(s, " ")
	return strings.TrimSpace(s)
}
