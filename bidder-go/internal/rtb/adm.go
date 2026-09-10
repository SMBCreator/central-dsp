package rtb

// Сборка adm-разметки — порт 1:1 backend/routers/rtb.py::_build_adm.
// Клик/показ идут через backend (/api/rtb/click, /api/rtb/imp) — биллинг и
// ОРД-статистика остаются в контрол-плейне; erid-маркировка обязательна для РФ.
//
// Audit R-05: все URL креатива (asset/click/icon/video/сторонние трекеры)
// проверяются admIncompat до ставки — только http(s), без кавычек/угловых
// скобок/пробелов — и экранируются по контексту вставки (HTML-атрибут,
// JS-строка внутри атрибута, CDATA). Клик-редирект /api/rtb/click больше не
// несёт цель в query (&r=…): цель — click_url креатива из bidmeta/БД, так что
// публичный токен бида нельзя превратить в open redirect с нашего домена.

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strings"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

// maxAdmURLLen — предел длины URL в разметке (Pydantic-валидатор — тот же).
const maxAdmURLLen = 2048

// clickMacros — макросы клика в HTML5-сниппете. Первая группа заменяется
// сырым URL, вторая — URL-encoded (для вложения в чужой редирект).
var (
	clickMacrosRaw = []string{"${CLICK_URL}", "%%CLICK_URL_UNESC%%", "{CLICK_URL}", "[CLICK_URL]", "${CLICK_URL_UNESC}"}
	clickMacrosEnc = []string{"${CLICK_URL_ENC}", "%%CLICK_URL_ESC%%", "${CLICK_URL_ESC}", "{CLICK_URL_ENC}"}
)

// safeURL — true, если s пригоден для вставки в разметку: абсолютный http(s)
// с хостом, не длиннее maxAdmURLLen, без пробелов, управляющих символов и
// кавычек/угловых скобок/обратного слэша (они и ломают контексты вставки,
// и нужны для инъекций). Макросы SSP вида ${AUCTION_PRICE} допустимы.
func safeURL(s string) bool {
	if s == "" || len(s) > maxAdmURLLen {
		return false
	}
	for _, r := range s {
		if r <= 0x20 || r == 0x7f || strings.ContainsRune(`"'<>\`+"`", r) {
			return false
		}
	}
	u, err := url.Parse(s)
	if err != nil {
		return false
	}
	scheme := strings.ToLower(u.Scheme)
	return (scheme == "http" || scheme == "https") && u.Host != ""
}

// admIncompat — "" если все URL креатива пригодны для разметки и
// пользовательский VAST разбирается, иначе причина (кандидат пропускается).
func admIncompat(c *feed.Candidate) string {
	if !safeURL(c.ClickURL) {
		return IncompatAdmURL
	}
	for _, u := range []string{c.ImpTrackerURL, c.ClickTrackerURL} {
		if u != "" && !safeURL(u) {
			return IncompatAdmURL
		}
	}
	switch c.Type {
	case "banner":
		if c.HTMLSnippet == "" && !safeURL(c.AssetURL) {
			return IncompatAdmURL
		}
	case "native", "push":
		for _, u := range []string{c.AssetURL, c.NativeIconURL} {
			if u != "" && !safeURL(u) {
				return IncompatAdmURL
			}
		}
	default: // video
		if c.VastXML != "" {
			if ads, err := scanVast(c.VastXML); err != nil || len(ads) == 0 {
				return IncompatAdmVAST
			}
		} else if !safeURL(c.VideoURL) {
			return IncompatAdmURL
		}
	}
	return ""
}

// BuildAdm собирает разметку креатива (banner HTML / native JSON / VAST XML).
// base — базовый URL backend'а, eventToken — подписанный HMAC-токен бида.
// Native — с фиксированными assets 1–5 (запрос без imp.native.request).
// Пустая строка — adm собрать нельзя (см. admIncompat); вызывающий код
// не отдаёт такой бид.
func BuildAdm(c *feed.Candidate, base, eventToken string) string {
	return BuildAdmAssets(c, base, eventToken, nil)
}

// BuildAdmAssets — как BuildAdm, но native assets заданы по imp.native.request
// (buildNativeAssets, audit R-04); nil = legacy фиксированный набор.
func BuildAdmAssets(c *feed.Candidate, base, eventToken string, nativeAssets []map[string]any) string {
	thirdImp := c.ImpTrackerURL
	thirdImpPx := ""
	if thirdImp != "" {
		thirdImpPx = fmt.Sprintf(`<img src="%s" width="1" height="1" style="display:none" alt=""/>`, htmlEscape(thirdImp))
	}
	thirdClick := c.ClickTrackerURL
	erid := c.Erid
	eridLabel := buildAdInfoBadge(c)
	// Токен — [0-9a-zA-Z._-], base — наш конфиг: URL ниже не нуждаются в
	// экранировании ни в атрибуте, ни в JS-строке, ни в CDATA.
	clickURL := fmt.Sprintf("%s/api/rtb/click?bid=%s", base, eventToken)
	clickBeacon := clickURL + "&beacon=1"
	impPixel := fmt.Sprintf("%s/api/rtb/imp?bid=%s", base, eventToken)
	viewURL := fmt.Sprintf("%s/api/rtb/view?bid=%s", base, eventToken)

	switch c.Type {
	case "banner":
		clickJS := ""
		if thirdClick != "" {
			clickJS = fmt.Sprintf(` onclick="(new Image()).src='%s'"`, jsStringEscape(thirdClick))
		}
		wrapID := "cdsp_" + admIDToken(eventToken)
		if c.HTMLSnippet != "" {
			snippet, hadMacro := substituteClickMacros(c.HTMLSnippet, clickURL)
			// Без макроса клик внутри сниппета всё равно фиксируем маячком на
			// обёртке (capture-фаза — stopPropagation внутри не помешает);
			// переход выполняет сам сниппет, cdsp_clid при этом не передаётся.
			beacon := ""
			if !hadMacro {
				beacon = clickBeacon
			}
			return fmt.Sprintf(`<div id="%s"><div id="%s_c">%s</div>%s</div><img src="%s" width="1" height="1" style="display:none"/>%s%s`,
				wrapID, wrapID, snippet, eridLabel, impPixel, thirdImpPx, viewScript(wrapID, viewURL, beacon))
		}
		w, h := 0, 0
		if c.Width != nil {
			w = *c.Width
		}
		if c.Height != nil {
			h = *c.Height
		}
		return fmt.Sprintf(`<div id="%s"><a href="%s" target="_blank"%s><img src="%s" width="%d" height="%d" alt=""/></a>%s</div><img src="%s" width="1" height="1" style="display:none"/>%s%s`,
			wrapID, clickURL, clickJS, htmlEscape(c.AssetURL), w, h, eridLabel, impPixel, thirdImpPx, viewScript(wrapID, viewURL, ""))

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
		assets := nativeAssets
		if assets == nil {
			assets = []map[string]any{
				{"id": 1, "title": map[string]any{"text": c.NativeTitle}},
				{"id": 2, "data": map[string]any{"type": 2, "value": c.NativeDesc}},
				{"id": 3, "img": map[string]any{"type": 3, "url": c.AssetURL}},
				{"id": 4, "img": map[string]any{"type": 1, "url": c.NativeIconURL}},
				{"id": 5, "data": map[string]any{"type": 12, "value": cta}},
			}
		}
		native := map[string]any{
			"link":        link,
			"imptrackers": imptrackers,
			// OpenRTB Native 1.2: event=2 (viewable MRC 50%), method=1 (img-пиксель).
			"eventtrackers": []map[string]any{{"event": 2, "methods": []int{1}, "url": viewURL}},
			"assets":        assets,
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
		vevent := fmt.Sprintf("%s/api/rtb/vevent?bid=%s", base, eventToken)
		if c.VastXML != "" {
			out, err := injectVastTrackers(c.VastXML, vastTrackers{
				Impressions:   []string{impPixel, thirdImp},
				VeventBase:    vevent,
				ClickTracking: []string{clickBeacon, thirdClick},
				ExtensionsXML: eridExt,
			})
			if err != nil {
				return ""
			}
			return out
		}
		duration := 15
		if c.VideoDuration != nil && *c.VideoDuration > 0 {
			duration = *c.VideoDuration
		}
		thirdImpNode := ""
		if thirdImp != "" {
			thirdImpNode = fmt.Sprintf("<Impression><![CDATA[%s]]></Impression>", thirdImp)
		}
		thirdClickNode := ""
		if thirdClick != "" {
			thirdClickNode = fmt.Sprintf("<ClickTracking><![CDATA[%s]]></ClickTracking>", thirdClick)
		}
		// ClickThrough — наш редирект (клик учитывается и лендинг получает
		// cdsp_clid), ClickTracking — маячок для плееров, которые открывают
		// ссылку сами; дедуп по биду делает backend (_once_txn).
		// MIME — по расширению video_url (то же значение проверяет videoIncompat
		// против imp.video.mimes), Duration — HH:MM:SS (VAST 3 §2.3.1.3).
		return fmt.Sprintf(`<VAST version="3.0"><Ad id="%s"><InLine>
<AdSystem>CentralDSP</AdSystem>%s<AdTitle>%s</AdTitle>
<Impression><![CDATA[%s]]></Impression>%s
<Creatives><Creative><Linear><Duration>%s</Duration>
<TrackingEvents>%s</TrackingEvents>
<VideoClicks><ClickThrough><![CDATA[%s]]></ClickThrough>
<ClickTracking><![CDATA[%s]]></ClickTracking>%s</VideoClicks>
<MediaFiles><MediaFile delivery="progressive" type="%s" width="640" height="360"><![CDATA[%s]]></MediaFile></MediaFiles>
</Linear></Creative></Creatives></InLine></Ad></VAST>`,
			xmlEscape(c.CreativeID), eridExt, xmlEscape(c.Name), impPixel, thirdImpNode, vastDuration(duration),
			vastTrackingNodes(vevent),
			clickURL, clickBeacon, thirdClickNode, creativeVideoMIME(c), c.VideoURL)
	}
}

// substituteClickMacros подставляет наш клик-URL вместо макросов клика в
// HTML5-сниппете. Второе значение — был ли хоть один макрос.
func substituteClickMacros(snippet, clickURL string) (string, bool) {
	had := false
	for _, m := range clickMacrosRaw {
		if strings.Contains(snippet, m) {
			had = true
			snippet = strings.ReplaceAll(snippet, m, clickURL)
		}
	}
	enc := url.QueryEscape(clickURL)
	for _, m := range clickMacrosEnc {
		if strings.Contains(snippet, m) {
			had = true
			snippet = strings.ReplaceAll(snippet, m, enc)
		}
	}
	return snippet, had
}

// viewScript — MRC viewability для баннера: >=50% площади в зоне видимости
// непрерывно >=1 сек (IntersectionObserver, фолбэк «страница видима» для
// старых webview). clickBeacon != "" — дополнительно фиксируем клик по
// содержимому обёртки (HTML5 без макроса). Порт rtb.py view_script.
func viewScript(wrapID, viewURL, clickBeacon string) string {
	click := ""
	if clickBeacon != "" {
		click = fmt.Sprintf(`var c=document.getElementById("%s_c");if(c){c.addEventListener("click",function(){(new Image()).src="%s";},true);}`, wrapID, clickBeacon)
	}
	return fmt.Sprintf(`<script>(function(){var f=0,t=null,el=document.getElementById("%s");`+
		`function fire(){if(f)return;f=1;(new Image()).src="%s";}`+
		`if(el&&"IntersectionObserver"in window){`+
		`var o=new IntersectionObserver(function(es){es.forEach(function(e){`+
		`if(e.intersectionRatio>=0.5){if(!t)t=setTimeout(function(){fire();o.disconnect();},1000);}`+
		`else if(t){clearTimeout(t);t=null;}});},{threshold:[0,0.5]});o.observe(el);}`+
		`else{setTimeout(fire,1000);}%s})();</script>`, wrapID, viewURL, click)
}

// admIDToken — первые 24 символа токена, пригодные для id/JS-строки
// (токен v1.<uuid>.<exp>.<hex> — только [0-9a-zA-Z._-]; остальное режем).
func admIDToken(tok string) string {
	var b strings.Builder
	for _, r := range tok {
		if b.Len() >= 24 {
			break
		}
		if r == '.' || r == '_' || r == '-' || (r >= '0' && r <= '9') || (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// vastDuration — секунды -> HH:MM:SS.
func vastDuration(sec int) string {
	if sec < 0 {
		sec = 0
	}
	return fmt.Sprintf("%02d:%02d:%02d", sec/3600, sec%3600/60, sec%60)
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

// htmlEscape экранирует спецсимволы HTML (текст и значения атрибутов).
func htmlEscape(s string) string {
	r := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&#39;")
	return r.Replace(s)
}

// jsStringEscape готовит строку к вложению в одинарные кавычки JS внутри
// HTML-атрибута: \xNN-последовательности не содержат ни кавычек, ни '&',
// ни угловых скобок, поэтому безопасны в обоих контекстах сразу.
func jsStringEscape(s string) string {
	r := strings.NewReplacer(`\`, `\\`, "'", `\x27`, `"`, `\x22`, "<", `\x3c`, ">", `\x3e`, "&", `\x26`, "\n", `\n`, "\r", `\r`)
	return r.Replace(s)
}

// xmlEscape экранирует спецсимволы XML (для VAST).
func xmlEscape(s string) string {
	r := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&apos;")
	return r.Replace(s)
}
