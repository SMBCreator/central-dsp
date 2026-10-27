package rtb

// Макросы ${CDSP_*} в ссылке перехода и пикселях: сценарии приёмки P1–P7
// (ТЗ trackers+macros, Фаза 1) и граничные случаи.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/macros"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
)

const macroBidReq = `{"id":"auc-1","test":1,"imp":[{"id":"1","tagid":"top_300x250","banner":{"w":300,"h":250},"bidfloor":1}],` +
	`"device":{"ifa":"dev-1","ua":"Mozilla/5.0","geo":{"country":"KAZ","region":"ALA","city":"Алматы"}},` +
	`"site":{"id":"12345","domain":"pub.example"}}`

// macroSnapshot — testSnapshot с макросами в URL единственного кандидата.
func macroSnapshot(click, impTracker, clickTracker string) *feed.Snapshot {
	snap := testSnapshot()
	c := &snap.Candidates[0]
	c.ClickURL = click
	c.ImpTrackerURL = impTracker
	c.ClickTrackerURL = clickTracker
	c.PrepareMacros()
	return snap
}

func bidAdm(t *testing.T, w *httptest.ResponseRecorder) string {
	t.Helper()
	if w.Code != http.StatusOK {
		t.Fatalf("want 200 bid, got %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		SeatBid []struct {
			Bid []struct {
				AdM string `json:"adm"`
			} `json:"bid"`
		} `json:"seatbid"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil || len(resp.SeatBid) == 0 || len(resp.SeatBid[0].Bid) == 0 {
		t.Fatalf("no bid in response: %v %s", err, w.Body.String())
	}
	return resp.SeatBid[0].Bid[0].AdM
}

func storedBidMeta(t *testing.T, keys []string, get func(string) (string, error)) map[string]any {
	t.Helper()
	for _, k := range keys {
		if strings.HasPrefix(k, "bidmeta:") {
			raw, err := get(k)
			if err != nil {
				t.Fatal(err)
			}
			var m map[string]any
			if err := json.Unmarshal([]byte(raw), &m); err != nil {
				t.Fatal(err)
			}
			return m
		}
	}
	t.Fatalf("bidmeta не записан; keys=%v", keys)
	return nil
}

// P1: пиксель показа с макросами разворачивается в adm (alpha-3 -> alpha-2).
func TestMacros_P1_ImpPixelExpanded(t *testing.T) {
	h, _ := newMiniRedisBidHandler(t, macroSnapshot("https://adv.example/promo", "https://t.example/i?c=${CDSP_COUNTRY}&s=${CDSP_SITE_ID}", ""))
	adm := bidAdm(t, doBid(t, h, macroBidReq))
	if !strings.Contains(adm, `src="https://t.example/i?c=KZ&amp;s=12345"`) {
		t.Fatalf("пиксель не развёрнут: %s", adm)
	}
	if strings.Contains(adm, "CDSP_") {
		t.Fatalf("в adm остался макрос: %s", adm)
	}
}

// P2: ссылка перехода — развёрнутая, с URL-кодированием и исходным регистром,
// лежит в bidmeta.click_url.
func TestMacros_P2_ClickLinkInBidMeta(t *testing.T) {
	h, mr := newMiniRedisBidHandler(t, macroSnapshot("https://l.example/?city=${CDSP_CITY}&p=${CDSP_PLACEMENT_ID}", "", ""))
	bidAdm(t, doBid(t, h, macroBidReq))
	meta := storedBidMeta(t, mr.Keys(), mr.Get)
	want := "https://l.example/?city=%D0%90%D0%BB%D0%BC%D0%B0%D1%82%D1%8B&p=top_300x250"
	if meta["click_url"] != want {
		t.Fatalf("bidmeta.click_url = %v, ждали %s", meta["click_url"], want)
	}
}

// P3: креатив без макросов — bidmeta без click_url, метрики макросов нулевые,
// а разметка совпадает с прежней BuildAdmAssets для всех форматов.
func TestMacros_P3_NoMacrosUnchanged(t *testing.T) {
	h, mr := newMiniRedisBidHandler(t, testSnapshot())
	reg := metrics.New()
	h.SetMetrics(reg)
	bidAdm(t, doBid(t, h, macroBidReq))
	if _, has := storedBidMeta(t, mr.Keys(), mr.Get)["click_url"]; has {
		t.Fatal("у креатива без макросов в bidmeta не должно быть click_url")
	}
	if got := metricValue(t, reg, `bidder_macro_expanded_total{field="click"}`); got != "0" {
		t.Fatalf("expanded click = %q, ждали 0", got)
	}

	for _, typ := range []string{"banner", "native", "push", "video"} {
		c := r05Candidate(typ)
		c.PrepareMacros()
		if c.HasMacros() {
			t.Fatalf("%s: макросов быть не должно", typ)
		}
		want := BuildAdmAssets(c, testBase, tok, nil)
		if got := buildAdmURLs(c, testBase, tok, nil, c.ImpTrackerURL, c.ClickTrackerURL); got != want || want == "" {
			t.Errorf("%s: разметка изменилась", typ)
		}
	}
}

// P5: макрос SSP внутри пикселя остаётся нетронутым, наш — разворачивается.
func TestMacros_P5_SSPMacroUntouched(t *testing.T) {
	h, _ := newMiniRedisBidHandler(t, macroSnapshot("https://adv.example/promo", "https://t.example/i?p=${AUCTION_PRICE}&c=${CDSP_COUNTRY}", ""))
	adm := bidAdm(t, doBid(t, h, macroBidReq))
	if !strings.Contains(adm, `p=${AUCTION_PRICE}&amp;c=KZ`) {
		t.Fatalf("ожидали p=${AUCTION_PRICE} и c=KZ: %s", adm)
	}
}

// P6: значение даёт URL длиннее 2048 — пиксель не вставлен, ставка есть,
// счётчик dropped вырос.
func TestMacros_P6_TooLongPixelDropped(t *testing.T) {
	h, _ := newMiniRedisBidHandler(t, macroSnapshot("https://adv.example/promo", "https://t.example/i?c=${CDSP_CITY}", ""))
	reg := metrics.New()
	h.SetMetrics(reg)
	body := strings.Replace(macroBidReq, "Алматы", strings.Repeat("a", 3000), 1)
	adm := bidAdm(t, doBid(t, h, body))
	if strings.Contains(adm, "t.example") {
		t.Fatalf("пиксель должен быть опущен: %s", adm)
	}
	if got := metricValue(t, reg, `bidder_macro_dropped_total{field="imp",reason="too_long"}`); got != "1" {
		t.Fatalf("dropped imp/too_long = %q, ждали 1", got)
	}
	if got := metricValue(t, reg, `bidder_macro_expanded_total{field="imp"}`); got != "1" {
		t.Fatalf("expanded imp = %q, ждали 1", got)
	}
}

// Слишком длинная ссылка перехода не пишется в bidmeta: клик пойдёт на click_url из БД.
func TestMacros_TooLongClickLinkFallsBack(t *testing.T) {
	h, mr := newMiniRedisBidHandler(t, macroSnapshot("https://l.example/?c=${CDSP_CITY}", "", ""))
	body := strings.Replace(macroBidReq, "Алматы", strings.Repeat("a", 3000), 1)
	bidAdm(t, doBid(t, h, body))
	if _, has := storedBidMeta(t, mr.Keys(), mr.Get)["click_url"]; has {
		t.Fatal("слишком длинная ссылка не должна попадать в bidmeta")
	}
}

// Развёрнутый URL перепроверяется safeURL: «KZ://…» не http(s) — пиксель опущен.
func TestMacros_UnsafeExpansionDropped(t *testing.T) {
	h := newTestHandler(nil)
	reg := metrics.New()
	h.SetMetrics(reg)
	c := &feed.Candidate{ImpTrackerURL: "${CDSP_COUNTRY}://t.example/i"}
	c.PrepareMacros()
	ctx := &macros.Context{}
	ctx.Set(macros.Country, "KZ")
	imp, _, _ := h.expandCreativeURLs(c, ctx)
	if imp != "" {
		t.Fatalf("небезопасный пиксель должен быть опущен, got %q", imp)
	}
	if got := metricValue(t, reg, `bidder_macro_dropped_total{field="imp",reason="unsafe"}`); got != "1" {
		t.Fatalf("dropped imp/unsafe = %q, ждали 1", got)
	}
}

// P7: нативный, push и видео — макросы развёрнуты во всех носителях пикселей.
func TestMacros_P7_AllFormats(t *testing.T) {
	h := newTestHandler(nil)
	ctx := &macros.Context{}
	ctx.Set(macros.Country, "KZ")
	ctx.Set(macros.SiteID, "12345")

	for _, typ := range []string{"native", "push", "video"} {
		c := r05Candidate(typ)
		c.ImpTrackerURL = "https://t.example/i?c=${CDSP_COUNTRY}"
		c.ClickTrackerURL = "https://t.example/c?s=${CDSP_SITE_ID}"
		c.PrepareMacros()
		imp, click, _ := h.expandCreativeURLs(c, ctx)
		adm := buildAdmURLs(c, testBase, tok, nil, imp, click)
		if strings.Contains(adm, "CDSP_") {
			t.Errorf("%s: в adm остался макрос: %s", typ, adm)
		}
		switch typ {
		case "video":
			if !strings.Contains(adm, "<Impression><![CDATA[https://t.example/i?c=KZ]]></Impression>") ||
				!strings.Contains(adm, "<ClickTracking><![CDATA[https://t.example/c?s=12345]]></ClickTracking>") {
				t.Errorf("video: нет развёрнутых Impression/ClickTracking: %s", adm)
			}
		default:
			var doc struct {
				Native struct {
					Imp  []string `json:"imptrackers"`
					Link struct {
						Click []string `json:"clicktrackers"`
					} `json:"link"`
				} `json:"native"`
			}
			if err := json.Unmarshal([]byte(adm), &doc); err != nil {
				t.Fatalf("%s: %v", typ, err)
			}
			if !contains(doc.Native.Imp, "https://t.example/i?c=KZ") || !contains(doc.Native.Link.Click, "https://t.example/c?s=12345") {
				t.Errorf("%s: нет развёрнутых imptrackers/clicktrackers: %s", typ, adm)
			}
		}
	}
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

// Контекст: значения каталога берутся из запроса; IP, идентификатор устройства,
// координаты, название SSP и цена клиринга недоступны (решение M3).
func TestMacros_ContextFromRequest(t *testing.T) {
	var req BidRequest
	if err := json.Unmarshal([]byte(macroBidReq), &req); err != nil {
		t.Fatal(err)
	}
	view := buildRequestView(&req)
	w, hgt := 300, 250
	c := &feed.Candidate{CampaignID: "7", LiID: "9", CreativeID: "6", Type: "banner", Width: &w, Height: &hgt}
	ctx := buildMacroContext(&req, &req.Imp[0], view, c, &feed.Partner{ID: "p-1"}, "bid-1")
	want := map[string]string{
		"BID_ID": "bid-1", "AUCTION_ID": "auc-1", "IMP_ID": "1", "CAMPAIGN_ID": "7", "GROUP_ID": "9", "CREATIVE_ID": "6",
		"SITE_ID": "12345", "PLACEMENT_ID": "top_300x250", "DOMAIN": "pub.example", "SSP_ID": "p-1",
		"COUNTRY": "KZ", "REGION": "ALA", "CITY": "Алматы", "AD_FORMAT": "banner",
		"SIZE": "300x250", "WIDTH": "300", "HEIGHT": "250", "BROWSER": "unknown", "CONNECTION": "unknown",
	}
	for name, v := range want {
		k, ok := macros.KeyOf(name)
		if !ok {
			t.Fatalf("нет макроса %s", name)
		}
		if got := ctx.V[k]; got != v {
			t.Errorf("%s = %q, ждали %q", name, got, v)
		}
	}
	for _, name := range []string{"TIMESTAMP", "CACHEBUSTER"} {
		k, _ := macros.KeyOf(name)
		if ctx.V[k] == "" {
			t.Errorf("%s должен быть заполнен", name)
		}
	}
}

func TestMacros_ConnectionName(t *testing.T) {
	for in, want := range map[int]string{0: "unknown", 1: "ethernet", 2: "wifi", 3: "cellular", 4: "cellular", 5: "cellular", 6: "cellular", 7: "cellular", 8: "unknown", -1: "unknown"} {
		if got := connectionName(in); got != want {
			t.Errorf("connectionName(%d) = %q, ждали %q", in, got, want)
		}
	}
}

// Бенчмарк сборки adm для кандидатов без макросов (≤5% к прежнему пути) и с ними.
func BenchmarkBuildAdm_NoMacros(b *testing.B) {
	c := r05Candidate("banner")
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if c.HasMacros() {
			b.Fatal("unexpected")
		}
		_ = buildAdmURLs(c, testBase, tok, nil, c.ImpTrackerURL, c.ClickTrackerURL)
	}
}

func BenchmarkBuildAdmAssets_Legacy(b *testing.B) {
	c := r05Candidate("banner")
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = BuildAdmAssets(c, testBase, tok, nil)
	}
}

func BenchmarkBuildAdm_WithMacros(b *testing.B) {
	h := newTestHandler(nil)
	c := r05Candidate("banner")
	c.ImpTrackerURL = "https://t.example/i?c=${CDSP_COUNTRY}&s=${CDSP_SITE_ID}&cb=${CDSP_CACHEBUSTER}"
	c.ClickURL = "https://l.example/?city=${CDSP_CITY}"
	c.PrepareMacros()
	ctx := &macros.Context{}
	ctx.Set(macros.Country, "KZ")
	ctx.Set(macros.SiteID, "12345")
	ctx.Set(macros.City, "Алматы")
	ctx.Set(macros.Cachebuster, "48201733")
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		imp, click, _ := h.expandCreativeURLs(c, ctx)
		_ = buildAdmURLs(c, testBase, tok, nil, imp, click)
	}
}
