package main

// Российский выход: клиент через socks5-туннель и проверка страны.
//
// Это не хвост антибот-лестницы, а условие работы первого слоя. Разведка 31.07:
// `kinoteatr.ru` (СИНЕМА ПАРК с сателлитами — Формула Кино, ОККО, Кронверк) с
// иностранного адреса просто рвёт соединение, а через туннель отвечает 200.
// Иллюзион ведёт себя так же, Люксор отдаёт 403 geoblocked. Значит прогон без
// туннеля недостоверен по построению — целые сети невидимы вовсе.
//
// Отсюда два требования, оба выполнены ниже. Во-первых, страна выхода
// ПРОВЕРЯЕТСЯ, а не предполагается: поднявшийся процесс xray ничего не
// доказывает, туннель может стоять и гнать трафик мимо. Во-вторых, несобранное
// из-за отсутствия туннеля считается ОТДЕЛЬНО от прочих причин — иначе в
// отчёте оно смешается с обычными отказами и будет выглядеть как «источники
// сломались», хотя мы просто не дотянулись.

import (
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

// ipCheckEndpoints — чем проверяем адрес выхода.
//
// `ip-api.com` намеренно не используется: лимит там считается на исходящий
// адрес, а выход у подписки общий, так что квоту мог выбрать кто угодно.
// Исчерпанная квота выглядит как пустой ответ без объяснений — то есть как
// поломка туннеля, которой нет.
var ipCheckEndpoints = []string{
	"https://checkip.amazonaws.com",
	"https://api.ipify.org",
}

// countryEndpoint — определение страны по адресу выхода.
//
// `ipapi.co` пробовался первым и отвечает 403 из этого окружения; `ipwho.is`
// на тот же адрес отдаёт 200 с JSON. Проверено живьём 31.07.
const countryEndpoint = "https://ipwho.is/%s"

// countryCode вытаскивает код страны из ответа ipwho.is.
var countryCodeRe = regexp.MustCompile(`"country_code"\s*:\s*"([A-Z]{2})"`)

// newTunnelClient — клиент, ходящий через socks5-прокси туннеля.
//
// Прокси задаётся явным адресом, а не переменными окружения: env-прокси в этом
// контейнере указывает на агент-прокси, и молчаливое наследование увело бы
// запросы мимо туннеля — ровно та ошибка, которая выглядит как «туннель не
// работает».
func newTunnelClient(proxyAddr string, timeoutSec, retries int) (*Client, error) {
	// Схема дописывается ДО разбора, а не после: url.Parse на голой паре
	// «хост:порт» не возвращает пустую схему, а падает с ошибкой — двоеточие он
	// принимает за разделитель схемы и спотыкается о цифры порта.
	raw := strings.TrimSpace(proxyAddr)
	if !strings.Contains(raw, "://") {
		raw = "socks5://" + raw
	}
	u, err := url.Parse(raw)
	if err != nil {
		return nil, fmt.Errorf("адрес прокси %q: %w", proxyAddr, err)
	}

	c := newClient(timeoutSec, retries)
	c.http = &http.Client{
		Timeout: time.Duration(timeoutSec) * time.Second,
		Transport: &http.Transport{
			Proxy: http.ProxyURL(u),
			// Наследование системного прокси выключено намеренно: см. выше.
			ProxyConnectHeader: http.Header{},
		},
	}
	return c, nil
}

// TunnelCheck — результат проверки выхода.
type TunnelCheck struct {
	IP      string
	Country string
	OK      bool
	Reason  string
}

// checkTunnel выясняет, куда на самом деле выходит клиент.
//
// Успехом считается смена страны, а не факт запуска процесса. Именно поэтому
// функция возвращает страну, а не булево «туннель поднят»: разница между
// «xray работает» и «трафик идёт через Россию» и есть то место, где проверка
// обычно врёт.
func checkTunnel(c *Client, wantCountry string) TunnelCheck {
	var ip string
	var lastErr error
	for _, ep := range ipCheckEndpoints {
		body, err := c.getText(ep)
		if err != nil {
			lastErr = err
			continue
		}
		if v := strings.TrimSpace(body); v != "" {
			ip = v
			break
		}
	}
	if ip == "" {
		reason := "адрес выхода не определён"
		if lastErr != nil {
			reason += ": " + lastErr.Error()
		}
		return TunnelCheck{Reason: reason}
	}

	body, err := c.getText(fmt.Sprintf(countryEndpoint, ip))
	if err != nil {
		// Адрес узнали, страну — нет. Это «проверка неприменима», а не
		// доказанный промах: врать в обе стороны тут одинаково вредно.
		return TunnelCheck{IP: ip, Reason: "страна не определена: " + err.Error()}
	}

	m := countryCodeRe.FindStringSubmatch(body)
	if len(m) < 2 {
		return TunnelCheck{IP: ip, Reason: "страна не разобрана из ответа сервиса"}
	}

	country := m[1]
	return TunnelCheck{
		IP:      ip,
		Country: country,
		OK:      country == strings.ToUpper(wantCountry),
		Reason:  "выход " + ip + ", страна " + country,
	}
}

// TunnelStats — счётчик несобранного из-за туннеля.
//
// Отдельно от прочих отказов: без туннеля площадка невидима не потому, что её
// источник сломан, а потому что мы до него не дотянулись. Смешать эти причины
// значит объявить чужую поломку там, где её нет.
type TunnelStats struct {
	Required int `json:"required"` // площадок, которым туннель нужен
	Skipped  int `json:"skipped"`  // из них не опрошено из-за отсутствия туннеля
}

// needsTunnel — площадки и сети, недоступные без российского выхода.
//
// Список от разведки, а не от догадки: у каждого пункта есть замеренный отказ.
// Люксору одного туннеля мало — за ним стоит JS-челлендж DDoS-Guard, который
// снимается только браузером, поэтому он помечен отдельно.
// Проверено повторно 04.08: kinoteatr.ru (СИНЕМА ПАРК) с иностранного адреса
// отвечает нормально — прежняя запись про обрыв соединения больше не
// соответствует действительности и снята. Люксор через туннель берётся обычным
// запросом: JS-челленджа за российским выходом не оказалось.
var needsTunnel = map[string]bool{
	kindIllusion:  true, // illusion-cinema.ru не резолвится с иностранного адреса
	kindAlmaz:     true, // то же поведение
	kindLuxor:     true, // 403 DDoS-Guard, снимается российским выходом
	kindTretyakov: true, // сайт галереи не резолвится с иностранного адреса
}

func requiresTunnel(sourceKind string) bool {
	return needsTunnel[strings.TrimSpace(sourceKind)]
}
