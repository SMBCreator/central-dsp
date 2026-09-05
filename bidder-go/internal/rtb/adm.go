package rtb

// Сборка adm-разметки — порт 1:1 backend/routers/rtb.py::_build_adm.
// Клик/показ идут через backend (/api/rtb/click, /api/rtb/imp) — биллинг и
// ОРД-статистика остаются в контрол-плейне; erid-маркировка обязательна для РФ.

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

// BuildAdm собирает разметку креатива (banner HTML / native JSON / VAST XML).
// base — базовый URL backend'а, eventToken — подписанный HMAC-токен бида.
func BuildAdm(c *feed.Candidate, base, eventToken string) string {
	thirdImp := c.ImpTrackerURL
	thirdImpPx := ""
	if thirdImp != "" {
		thirdImpPx = fmt.Sprintf(`<img src="%s" width="1" height="1" style="display:none" alt=""/>`, thirdImp)
	}
	thirdClick := c.ClickTrackerURL
	erid := c.Erid
	eridLabel := buildAdInfoBadge(c)
	clickURL := fmt.Sprintf("%s/api/rtb/click?bid=%s&r=%s", base, eventToken, c.ClickURL)
	impPixel := fmt.Sprintf("%s/api/rtb/imp?bid=%s", base, eventToken)

	switch c.Type {
	case "banner":
		clickJS := ""
		if thirdClick != "" {
			clickJS = fmt.Sprintf(` onclick="(new Image()).src='%s'"`, thirdClick)
		}
		if c.HTMLSnippet != "" {
			return c.HTMLSnippet + eridLabel +
				fmt.Sprintf(`<img src="%s" width="1" height="1" style="display:none"/>`, impPixel) + thirdImpPx
		}
		w, h := 0, 0
		if c.Width != nil {
			w = *c.Width
		}
		if c.Height != nil {
			h = *c.Height
		}
		return fmt.Sprintf(`<a href="%s" target="_blank"%s><img src="%s" width="%d" height="%d" alt=""/></a>%s<img src="%s" width="1" height="1" style="display:none"/>%s`,
			clickURL, clickJS, c.AssetURL, w, h, eridLabel, impPixel, thirdImpPx)

	case "native", "push":
		link := map[string]any{"url": clickURL}
		if thirdClick != "" {
			link["clicktrackers"] = []string{thirdClick}
		}
		imptrackers := []string{impPixel}
		if thirdImp != "" {
			imptrackers = append(imptrackers, thirdImp)
		}
		cta := c.NativeCTA
		if cta == "" {
			cta = "Подробнее"
		}
		native := map[string]any{
			"link":        link,
			"imptrackers": imptrackers,
			"assets": []map[string]any{
				{"id": 1, "title": map[string]any{"text": c.NativeTitle}},
				{"id": 2, "data": map[string]any{"type": 2, "value": c.NativeDesc}},
				{"id": 3, "img": map[string]any{"type": 3, "url": c.AssetURL}},
				{"id": 4, "img": map[string]any{"type": 1, "url": c.NativeIconURL}},
				{"id": 5, "data": map[string]any{"type": 12, "value": cta}},
			},
		}
		ext := map[string]any{}
		if erid != "" {
			ext["erid"] = erid
		}
		if adv := advertiserInfoMap(c); adv != nil {
			ext["advertiser_info"] = adv
		}
		if c.Type == "push" {
			ext["ad_format"] = "push"
		}
		if len(ext) > 0 {
			native["ext"] = ext
		}
		b, _ := json.Marshal(map[string]any{"native": native})
		return string(b)

	default: // video
		eridExt := ""
		if erid != "" || c.AdvName != "" {
			eridExt = fmt.Sprintf(`<Extensions><Extension type="erid"><Erid>%s</Erid></Extension><Extension type="AdvertiserInfo"><Name>%s</Name><INN>%s</INN><OGRN>%s</OGRN><Address>%s</Address></Extension></Extensions>`,
				xmlEscape(erid), xmlEscape(c.AdvName), xmlEscape(c.AdvINN), xmlEscape(c.AdvOGRN), xmlEscape(c.AdvAddress))
		}
		if c.VastXML != "" {
			return injectVastExtensions(c.VastXML, eridExt)
		}
		duration := 15
		if c.VideoDuration != nil && *c.VideoDuration > 0 {
			duration = *c.VideoDuration
		}
		vevent := fmt.Sprintf("%s/api/rtb/vevent?bid=%s", base, eventToken)
		thirdImpNode := ""
		if thirdImp != "" {
			thirdImpNode = fmt.Sprintf("<Impression><![CDATA[%s]]></Impression>", thirdImp)
		}
		thirdClickNode := ""
		if thirdClick != "" {
			thirdClickNode = fmt.Sprintf("<ClickTracking><![CDATA[%s]]></ClickTracking>", thirdClick)
		}
		return fmt.Sprintf(`<VAST version="3.0"><Ad id="%s"><InLine>
<AdSystem>CentralDSP</AdSystem>%s<AdTitle>%s</AdTitle>
<Impression><![CDATA[%s]]></Impression>%s
<Creatives><Creative><Linear><Duration>00:00:%02d</Duration>
<TrackingEvents>
<Tracking event="start"><![CDATA[%s&e=start]]></Tracking>
<Tracking event="firstQuartile"><![CDATA[%s&e=firstQuartile]]></Tracking>
<Tracking event="midpoint"><![CDATA[%s&e=midpoint]]></Tracking>
<Tracking event="thirdQuartile"><![CDATA[%s&e=thirdQuartile]]></Tracking>
<Tracking event="complete"><![CDATA[%s&e=complete]]></Tracking>
<Tracking event="skip"><![CDATA[%s&e=skip]]></Tracking>
</TrackingEvents>
<VideoClicks><ClickThrough><![CDATA[%s]]></ClickThrough>
<ClickTracking><![CDATA[%s]]></ClickTracking>%s</VideoClicks>
<MediaFiles><MediaFile delivery="progressive" type="video/mp4" width="640" height="360"><![CDATA[%s]]></MediaFile></MediaFiles>
</Linear></Creative></Creatives></InLine></Ad></VAST>`,
			c.CreativeID, eridExt, c.Name, impPixel, thirdImpNode, duration,
			vevent, vevent, vevent, vevent, vevent, vevent,
			c.ClickURL, clickURL, thirdClickNode, c.VideoURL)
	}
}

// injectVastExtensions вставляет блок erid/AdvertiserInfo в пользовательский
// VAST (creatives.vast_xml). erid выдан ОРД именно на этот ролик, и без
// Extension площадка показала бы его как немаркированный. Уже промаркированный
// VAST (есть <Erid>) не трогаем; без <InLine>/<Wrapper> — отдаём как есть.
func injectVastExtensions(vast, ext string) string {
	if ext == "" || strings.Contains(vast, "<Erid>") {
		return vast
	}
	for _, open := range []string{"<InLine>", "<Wrapper>"} {
		if i := strings.Index(vast, open); i >= 0 {
			at := i + len(open)
			return vast[:at] + ext + vast[at:]
		}
	}
	return vast
}

// advertiserInfoMap возвращает данные рекламодателя для native ext (38-ФЗ), либо nil.
func advertiserInfoMap(c *feed.Candidate) map[string]string {
	if c.AdvName == "" && c.AdvINN == "" && c.AdvOGRN == "" {
		return nil
	}
	m := map[string]string{}
	if c.AdvName != "" {
		m["name"] = c.AdvName
	}
	if c.AdvINN != "" {
		m["inn"] = c.AdvINN
	}
	if c.AdvOGRN != "" {
		m["ogrn"] = c.AdvOGRN
	}
	if c.AdvAddress != "" {
		m["address"] = c.AdvAddress
	}
	return m
}

// buildAdInfoBadge собирает рекламное меню для баннера: уголок «Реклама ⓘ»,
// по клику раскрывается панель с erid и сведениями о рекламодателе (38-ФЗ).
// Без внешних зависимостей; id уникален по creative id, чтобы не конфликтовать
// при нескольких баннерах на странице.
func buildAdInfoBadge(c *feed.Candidate) string {
	if c.Erid == "" && c.AdvName == "" {
		return ""
	}
	uid := c.CreativeID
	if len(uid) > 8 {
		uid = uid[:8]
	}
	rows := ""
	if c.Erid != "" {
		rows += fmt.Sprintf(`<div>erid: %s</div>`, htmlEscape(c.Erid))
	}
	if c.AdvName != "" {
		rows += fmt.Sprintf(`<div>%s</div>`, htmlEscape(c.AdvName))
	}
	if c.AdvINN != "" {
		rows += fmt.Sprintf(`<div>ИНН: %s</div>`, htmlEscape(c.AdvINN))
	}
	if c.AdvOGRN != "" {
		rows += fmt.Sprintf(`<div>ОГРН: %s</div>`, htmlEscape(c.AdvOGRN))
	}
	if c.AdvAddress != "" {
		rows += fmt.Sprintf(`<div>%s</div>`, htmlEscape(c.AdvAddress))
	}
	return fmt.Sprintf(`<span id="cdsp-ai-%s" style="position:absolute;top:2px;left:2px;z-index:9;font:10px/1.4 sans-serif">`+
		`<span onclick="var p=document.getElementById('cdsp-ap-%s');p.style.display=p.style.display==='none'?'block':'none';event.preventDefault();event.stopPropagation();" `+
		`style="cursor:pointer;background:rgba(0,0,0,.55);color:#fff;padding:1px 5px;border-radius:2px;user-select:none">Реклама &#9432;</span>`+
		`<span id="cdsp-ap-%s" style="display:none;position:absolute;top:16px;left:0;background:rgba(0,0,0,.85);color:#fff;padding:6px 8px;border-radius:3px;min-width:180px;max-width:260px;white-space:normal;word-break:break-word">%s</span></span>`,
		uid, uid, uid, rows)
}

// htmlEscape экранирует спецсимволы HTML.
func htmlEscape(s string) string {
	r := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&#39;")
	return r.Replace(s)
}

// xmlEscape экранирует спецсимволы XML (для VAST).
func xmlEscape(s string) string {
	r := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&apos;")
	return r.Replace(s)
}
