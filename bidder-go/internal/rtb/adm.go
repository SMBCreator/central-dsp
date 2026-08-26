package rtb

// Сборка adm-разметки — порт 1:1 backend/routers/rtb.py::_build_adm.
// Клик/показ идут через backend (/api/rtb/click, /api/rtb/imp) — биллинг и
// ОРД-статистика остаются в контрол-плейне; erid-маркировка обязательна для РФ.

import (
	"encoding/json"
	"fmt"

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
	eridLabel := ""
	if erid != "" {
		eridLabel = fmt.Sprintf(`<span style="position:absolute;top:2px;left:2px;background:rgba(0,0,0,.55);color:#fff;font:10px/1.4 sans-serif;padding:1px 4px;border-radius:2px;z-index:9">Реклама erid: %s</span>`, erid)
	}
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
		if c.Type == "push" {
			ext["ad_format"] = "push"
		}
		if len(ext) > 0 {
			native["ext"] = ext
		}
		b, _ := json.Marshal(map[string]any{"native": native})
		return string(b)

	default: // video
		if c.VastXML != "" {
			return c.VastXML
		}
		duration := 15
		if c.VideoDuration != nil && *c.VideoDuration > 0 {
			duration = *c.VideoDuration
		}
		vevent := fmt.Sprintf("%s/api/rtb/vevent?bid=%s", base, eventToken)
		eridExt := ""
		if erid != "" {
			eridExt = fmt.Sprintf(`<Extensions><Extension type="erid"><Erid>%s</Erid></Extension></Extensions>`, erid)
		}
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
