// OpenRTB-совместимость (audit R-04): PMP-сделки, native assets по запросу,
// video MIME/protocol/duration. Правила — общие с backend/rtb_compat.py
// (Python — эталон); фикстуры testdata/openrtb/*.json гоняются обоими.
//
// Принцип: неподдерживаемый запрос -> явный отказ с причиной в
// bidder_openrtb_incompat_total{reason}, а не заведомо невалидная ставка.
package rtb

import (
	"encoding/json"
	"path"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

// Причины несовместимости (метки метрики; те же строки в Python).
const (
	IncompatNativeMalformed = "native_request_malformed"
	IncompatNativeAsset     = "native_asset_missing"
	IncompatVideoMIME       = "video_mime"
	IncompatVideoProtocol   = "video_protocol"
	IncompatVideoDuration   = "video_duration"
	IncompatPrivateAuction  = "private_auction"
	// Audit R-05 (adm.go admIncompat): разметку не собрать безопасно.
	IncompatAdmURL  = "adm_url_invalid"    // URL креатива не http(s) или с опасными символами
	IncompatAdmVAST = "adm_vast_malformed" // vast_xml не разбирается / без InLine|Wrapper
)

// IncompatReasons — полный список для регистрации метрики.
var IncompatReasons = []string{
	IncompatNativeMalformed, IncompatNativeAsset,
	IncompatVideoMIME, IncompatVideoProtocol, IncompatVideoDuration,
	IncompatPrivateAuction,
}

// ---------- Native (OpenRTB Native 1.1/1.2) ----------

// NativeRequest — разобранный imp.native.request.
type NativeRequest struct {
	Assets []NativeReqAsset `json:"assets"`
}

type NativeReqAsset struct {
	ID       int              `json:"id"`
	Required int              `json:"required"`
	Title    *NativeReqTitle  `json:"title,omitempty"`
	Img      *NativeReqImg    `json:"img,omitempty"`
	Data     *NativeReqData   `json:"data,omitempty"`
	Video    *json.RawMessage `json:"video,omitempty"`
}

type NativeReqTitle struct {
	Len int `json:"len"`
}

type NativeReqImg struct {
	Type int `json:"type"` // 1 icon, 2 logo (deprecated), 3 main
	W    int `json:"w"`
	H    int `json:"h"`
	WMin int `json:"wmin"`
	HMin int `json:"hmin"`
}

type NativeReqData struct {
	Type int `json:"type"` // 1 sponsored, 2 desc, 12 cta …
	Len  int `json:"len"`
}

// parseNativeRequest разбирает imp.native.request: по спецификации — строка
// JSON, у некоторых SSP — вложенный объект; допускается обёртка {"native":{…}}.
// nil, nil — запроса нет (legacy: фиксированные assets 1–5).
func parseNativeRequest(n *Native) (*NativeRequest, error) {
	if n == nil {
		return nil, nil
	}
	raw := strings.TrimSpace(n.Request)
	if raw == "" {
		if len(n.RequestObj) > 0 {
			raw = string(n.RequestObj)
		} else {
			return nil, nil
		}
	}
	var wrapper struct {
		Native *NativeRequest `json:"native"`
	}
	if err := json.Unmarshal([]byte(raw), &wrapper); err == nil && wrapper.Native != nil {
		return wrapper.Native, nil
	}
	var req NativeRequest
	if err := json.Unmarshal([]byte(raw), &req); err != nil {
		return nil, err
	}
	return &req, nil
}

// buildNativeAssets собирает assets ровно по запрошенным id/типам.
// false — обязательный asset нечем заполнить (или заполнить нечего вообще).
func buildNativeAssets(req *NativeRequest, c *feed.Candidate) ([]map[string]any, bool) {
	cta := c.NativeCTA
	if cta == "" {
		cta = "Подробнее"
	}
	var out []map[string]any
	for _, a := range req.Assets {
		var body map[string]any
		switch {
		case a.Title != nil:
			if c.NativeTitle != "" {
				body = map[string]any{"title": map[string]any{"text": truncRunes(c.NativeTitle, a.Title.Len)}}
			}
		case a.Img != nil:
			url := ""
			switch a.Img.Type {
			case 3:
				url = c.AssetURL
			case 1, 2:
				url = c.NativeIconURL
			}
			if url != "" {
				img := map[string]any{"url": url}
				if a.Img.Type == 3 && c.Width != nil && c.Height != nil && *c.Width > 0 && *c.Height > 0 {
					img["w"], img["h"] = *c.Width, *c.Height
				}
				body = map[string]any{"img": img}
			}
		case a.Data != nil:
			val := ""
			switch a.Data.Type {
			case 2:
				val = c.NativeDesc
			case 12:
				val = cta
			case 1:
				val = c.AdvName
			}
			if val != "" {
				body = map[string]any{"data": map[string]any{"value": truncRunes(val, a.Data.Len)}}
			}
		}
		if body == nil {
			if a.Required == 1 {
				return nil, false
			}
			continue
		}
		body["id"] = a.ID
		out = append(out, body)
	}
	return out, len(out) > 0
}

func truncRunes(s string, n int) string {
	if n <= 0 || utf8.RuneCountInString(s) <= n {
		return s
	}
	r := []rune(s)
	return string(r[:n])
}

// ---------- Video ----------

var (
	reMediaType   = regexp.MustCompile(`<MediaFile[^>]*\btype="([^"]+)"`)
	reVastVersion = regexp.MustCompile(`<VAST[^>]*\bversion="(\d)`)
)

var extMIME = map[string]string{
	".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
	".mov": "video/quicktime", ".m3u8": "application/x-mpegURL",
	".mpd": "application/dash+xml", ".3gp": "video/3gpp", ".ogv": "video/ogg",
}

// creativeVideoMIME — MIME ролика: из vast_xml (первый MediaFile), иначе из
// расширения video_url; по умолчанию video/mp4 (так рендерит наш VAST).
func creativeVideoMIME(c *feed.Candidate) string {
	if c.VastXML != "" {
		if m := reMediaType.FindStringSubmatch(c.VastXML); m != nil {
			return m[1]
		}
	}
	u := c.VideoURL
	if i := strings.IndexAny(u, "?#"); i >= 0 {
		u = u[:i]
	}
	if mime, ok := extMIME[strings.ToLower(path.Ext(u))]; ok {
		return mime
	}
	return "video/mp4"
}

// creativeVASTProtocols — какие imp.video.protocols обслуживает креатив
// (§5.8: 2/5 = VAST 2 / wrapper, 3/6 = VAST 3, 7/8 = VAST 4). Наш генератор — 3.0.
func creativeVASTProtocols(c *feed.Candidate) []int {
	ver := "3"
	if c.VastXML != "" {
		if m := reVastVersion.FindStringSubmatch(c.VastXML); m != nil {
			ver = m[1]
		}
	}
	switch ver {
	case "2":
		return []int{2, 5}
	case "4":
		return []int{7, 8}
	default:
		return []int{3, 6}
	}
}

// creativeVideoDuration — длительность для проверки min/maxduration; 0 = неизвестна
// (пользовательский VAST без video_duration — не проверяем).
func creativeVideoDuration(c *feed.Candidate) int {
	if c.VideoDuration != nil && *c.VideoDuration > 0 {
		return *c.VideoDuration
	}
	if c.VastXML != "" {
		return 0
	}
	return 15 // дефолт генератора VAST
}

// videoIncompat — "" если креатив совместим со слотом, иначе причина.
func videoIncompat(v *Video, c *feed.Candidate) string {
	if v == nil {
		return ""
	}
	if len(v.Mimes) > 0 && !containsFold(v.Mimes, creativeVideoMIME(c)) {
		return IncompatVideoMIME
	}
	if len(v.Protocols) > 0 && !intersectsInt(v.Protocols, creativeVASTProtocols(c)) {
		return IncompatVideoProtocol
	}
	if d := creativeVideoDuration(c); d > 0 {
		if v.MinDuration > 0 && d < v.MinDuration {
			return IncompatVideoDuration
		}
		if v.MaxDuration > 0 && d > v.MaxDuration {
			return IncompatVideoDuration
		}
	}
	return ""
}

func containsFold(list []string, v string) bool {
	for _, x := range list {
		if strings.EqualFold(strings.TrimSpace(x), v) {
			return true
		}
	}
	return false
}

func intersectsInt(a, b []int) bool {
	for _, x := range a {
		for _, y := range b {
			if x == y {
				return true
			}
		}
	}
	return false
}

// ---------- PMP ----------

// matchedDealObj — первая сделка imp.pmp.deals из настроенных в line item
// (порядок — как в targeting.MatchedDeal / rtb.py:_matched_deal).
func matchedDealObj(imp *Imp, dealIDs []string) *Deal {
	if imp.PMP == nil || len(dealIDs) == 0 {
		return nil
	}
	for _, want := range dealIDs {
		for i := range imp.PMP.Deals {
			if imp.PMP.Deals[i].ID == want {
				return &imp.PMP.Deals[i]
			}
		}
	}
	return nil
}

// privateAuctionOnly — imp.pmp.private_auction=1: торгуются только сделки.
func privateAuctionOnly(imp *Imp) bool {
	return imp.PMP != nil && imp.PMP.PrivateAuction == 1
}
