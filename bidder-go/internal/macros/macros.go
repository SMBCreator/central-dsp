// Пакет macros — подстановка наших макросов ${CDSP_<ИМЯ>} в ссылку перехода и
// пиксели креатива. Каталог (имена, группы, примеры) — backend/url_macros.json;
// Go-таблица Names обязана с ним совпадать (TestCatalogParity). Эталон на
// Python — backend/url_macros.py; общие векторы — testdata/macro_cases.json.
//
// Правила (docs/CONTRACTS.md, «Макросы в ссылках»):
//   - шаблон разбирается один раз (при загрузке фида), на ставке — только
//     склейка строк без сети и Redis;
//   - значения всегда URL-кодируются (%XX, пробел — %20, не «+»);
//   - нет значения — пустая строка;
//   - макрос не из каталога (опечатка, чужой синтаксис, другой регистр)
//     остаётся в строке как есть;
//   - значения не разворачиваются повторно: макрос внутри значения — просто текст.
package macros

import (
	"net/url"
	"strings"
)

// Key — индекс макроса в каталоге (позиция в Names и в Context.V).
type Key uint8

const (
	BidID Key = iota
	AuctionID
	ImpID
	CampaignID
	GroupID
	CreativeID
	SiteID
	PublisherID
	PlacementID
	Domain
	Bundle
	SSPID
	Country
	Region
	City
	DeviceType
	OS
	OSVersion
	Browser
	DeviceMake
	DeviceModel
	Carrier
	Connection
	Language
	AdFormat
	Size
	Width
	Height
	Timestamp
	Cachebuster
	numKeys
)

// Names — имена макросов без префикса, в порядке констант Key. Порядок
// совпадает с backend/url_macros.json.
var Names = [numKeys]string{
	"BID_ID", "AUCTION_ID", "IMP_ID", "CAMPAIGN_ID", "GROUP_ID", "CREATIVE_ID",
	"SITE_ID", "PUBLISHER_ID", "PLACEMENT_ID", "DOMAIN", "BUNDLE", "SSP_ID",
	"COUNTRY", "REGION", "CITY",
	"DEVICE_TYPE", "OS", "OS_VERSION", "BROWSER", "DEVICE_MAKE", "DEVICE_MODEL", "CARRIER", "CONNECTION", "LANGUAGE",
	"AD_FORMAT", "SIZE", "WIDTH", "HEIGHT",
	"TIMESTAMP", "CACHEBUSTER",
}

const prefix = "${CDSP_"

var byName = func() map[string]Key {
	m := make(map[string]Key, numKeys)
	for i, n := range Names {
		m[n] = Key(i)
	}
	return m
}()

// KeyOf — ключ каталога по имени без префикса («COUNTRY»); регистр значим.
func KeyOf(name string) (Key, bool) {
	k, ok := byName[name]
	return k, ok
}

// Context — значения макросов одной ставки. Заполняется из запроса и кандидата
// без обращений к сети; пустое значение подставится пустой строкой.
type Context struct {
	V [numKeys]string
}

// Set задаёт значение макроса.
func (c *Context) Set(k Key, v string) { c.V[k] = v }

type segment struct {
	lit   string
	key   Key
	isKey bool
}

// Template — разобранная строка с макросами. nil означает «в строке нет ни
// одного макроса из каталога»: вызывающий код берёт исходную строку как есть.
type Template struct {
	segs []segment
}

// Parse разбирает строку один раз. Без макросов из каталога возвращает nil
// (быстрый путь без аллокаций на ставке).
func Parse(s string) *Template {
	if !strings.Contains(s, prefix) {
		return nil
	}
	var segs []segment
	litStart := 0 // начало ещё не сброшенного статического текста
	i := 0
	for {
		j := strings.Index(s[i:], prefix)
		if j < 0 {
			break
		}
		start := i + j
		end := strings.IndexByte(s[start:], '}')
		if end < 0 {
			break
		}
		end += start
		name := s[start+len(prefix) : end]
		if k, ok := byName[name]; ok {
			if start > litStart {
				segs = append(segs, segment{lit: s[litStart:start]})
			}
			segs = append(segs, segment{key: k, isKey: true})
			litStart = end + 1
			i = end + 1
			continue
		}
		// Неизвестный макрос остаётся текстом; поиск продолжаем со следующего
		// символа, чтобы поймать вложенный известный (${CDSP_${CDSP_CITY}}).
		i = start + 1
	}
	if len(segs) == 0 {
		return nil
	}
	if litStart < len(s) {
		segs = append(segs, segment{lit: s[litStart:]})
	}
	return &Template{segs: segs}
}

// Expand подставляет значения контекста. Вызывать только для не-nil шаблона.
func (t *Template) Expand(ctx *Context) string {
	var b strings.Builder
	n := 0
	for _, sg := range t.segs {
		if sg.isKey {
			n += len(ctx.V[sg.key]) * 3
		} else {
			n += len(sg.lit)
		}
	}
	b.Grow(n)
	for _, sg := range t.segs {
		if !sg.isKey {
			b.WriteString(sg.lit)
			continue
		}
		if v := ctx.V[sg.key]; v != "" {
			b.WriteString(escape(v))
		}
	}
	return b.String()
}

// escape — URL-кодирование значения: всё, кроме [A-Za-z0-9-_.~], в %XX;
// пробел — %20 (url.QueryEscape даёт «+», а «+» из значения кодирует как %2B,
// поэтому замена безопасна).
func escape(v string) string {
	return strings.ReplaceAll(url.QueryEscape(v), "+", "%20")
}
