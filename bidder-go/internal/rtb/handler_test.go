package rtb

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

// newTestHandler создаёт handler без Redis (degraded mode: QPS/freq/budget
// гейты пропускаются) и с seed-снапшотом фида.
func newTestHandler(snap *feed.Snapshot) *Handler {
	store := feed.NewStore(feed.Config{BackendURL: "http://localhost:0"})
	if snap != nil {
		store.SetSnapshot(snap)
	}
	return NewHandler(store, nil)
}

func testSnapshot() *feed.Snapshot {
	w, h := 300, 250
	return &feed.Snapshot{
		Candidates: []feed.Candidate{{
			LiID:       "11",
			BidPrice:   120.0, // CPM RUB
			BidType:    "cpm",
			CampaignID: "1",
			OwnerID:    "7",
			CampBudget: 1000,
			CreativeID: "21",
			Type:       "banner",
			Width:      &w,
			Height:     &h,
			AssetURL:   "https://cdn.example/banner.png",
			ClickURL:   "https://adv.example/promo",
			Erid:       "2Vfnxx",
			AdvDomain:  "adv.example",
			Name:       "Test Banner",
		}},
		Partners: map[string]*feed.Partner{
			"key1": {ID: "5", EndpointKey: "key1", Status: "active", BidFloor: 10},
		},
		GBlack:   map[string]map[string]bool{},
		GWhite:   map[string]map[string]bool{},
		LoadedAt: time.Now(),
	}
}

func doBid(t *testing.T, h *Handler, body string) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(http.MethodPost, "/rtb/bid/key1", strings.NewReader(body))
	r.SetPathValue("endpointKey", "key1")
	w := httptest.NewRecorder()
	h.Bid(w, r)
	return w
}

func TestBidEmptyFeedUnknownEndpoint(t *testing.T) {
	// NewStore стартует с пустым снапшотом: неизвестный endpointKey -> 404
	// (паритет с Python: партнёр не найден).
	h := newTestHandler(nil)
	w := doBid(t, h, `{"id":"r1","imp":[{"id":"1"}]}`)
	if w.Code != http.StatusNotFound {
		t.Fatalf("want 404, got %d", w.Code)
	}
}

func TestBidInvalidJSON(t *testing.T) {
	h := newTestHandler(testSnapshot())
	w := doBid(t, h, "{not json")
	if w.Code != http.StatusBadRequest {
		t.Fatalf("want 400, got %d", w.Code)
	}
}

func TestBidNoImpsIsNoBid(t *testing.T) {
	h := newTestHandler(testSnapshot())
	w := doBid(t, h, `{"id":"r1","imp":[]}`)
	if w.Code != http.StatusNoContent {
		t.Fatalf("want 204 no-bid, got %d", w.Code)
	}
}

func TestBidUnknownEndpoint(t *testing.T) {
	h := newTestHandler(testSnapshot())
	r := httptest.NewRequest(http.MethodPost, "/rtb/bid/nope", strings.NewReader(`{"id":"r1","imp":[{"id":"1"}]}`))
	r.SetPathValue("endpointKey", "nope")
	w := httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusNotFound {
		t.Fatalf("want 404, got %d", w.Code)
	}
}

func TestBidBannerMatchReturnsBid(t *testing.T) {
	h := newTestHandler(testSnapshot())
	body := `{"id":"r2","at":1,"imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":10.5}],
		"site":{"domain":"news.example","cat":["IAB12"]},
		"device":{"ua":"Mozilla","os":"android","devicetype":1,"geo":{"country":"RUS"},"ifa":"ifa-1"}}`
	w := doBid(t, h, body)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200 bid, got %d body=%s", w.Code, w.Body.String())
	}
	var resp BidResponse
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatalf("bad response json: %v", err)
	}
	if resp.Cur != "RUB" {
		t.Fatalf("want cur RUB, got %q", resp.Cur)
	}
	if len(resp.SeatBid) != 1 || len(resp.SeatBid[0].Bid) != 1 {
		t.Fatalf("want 1 bid, got %+v", resp)
	}
	bid := resp.SeatBid[0].Bid[0]
	if bid.Price < 10.5 {
		t.Fatalf("price below floor: %v", bid.Price)
	}
	if bid.Price > 120.0 {
		t.Fatalf("price above raw bid: %v", bid.Price)
	}
	if !strings.Contains(bid.AdM, "erid: 2Vfnxx") {
		t.Fatalf("adm missing erid label: %s", bid.AdM)
	}
	if !strings.Contains(bid.NURL, "/api/rtb/win?bid=v1.") {
		t.Fatalf("nurl not signed: %s", bid.NURL)
	}
	if bid.W != 300 || bid.H != 250 {
		t.Fatalf("size mismatch: %dx%d", bid.W, bid.H)
	}
}

func TestBidFloorAboveBidIsNoBid(t *testing.T) {
	h := newTestHandler(testSnapshot())
	body := `{"id":"r3","imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":500}]}`
	w := doBid(t, h, body)
	if w.Code != http.StatusNoContent {
		t.Fatalf("want 204 (floor 500 > bid 120), got %d", w.Code)
	}
}

func TestBidWrongSizeIsNoBid(t *testing.T) {
	h := newTestHandler(testSnapshot())
	body := `{"id":"r4","imp":[{"id":"1","banner":{"w":728,"h":90}}]}`
	w := doBid(t, h, body)
	if w.Code != http.StatusNoContent {
		t.Fatalf("want 204 (728x90 vs 300x250), got %d", w.Code)
	}
}

func TestBidGeoMismatchIsNoBid(t *testing.T) {
	snap := testSnapshot()
	tg := &feed.Targeting{GeoCountries: []string{"RUS"}}
	snap.Candidates[0].T = tg
	h := newTestHandler(snap)
	body := `{"id":"r5","imp":[{"id":"1","banner":{"w":300,"h":250}}],
		"device":{"geo":{"country":"USA"}}}`
	w := doBid(t, h, body)
	if w.Code != http.StatusNoContent {
		t.Fatalf("want 204 (geo USA vs RUS), got %d", w.Code)
	}
}

func TestBidBAdvBlocksAdvertiser(t *testing.T) {
	h := newTestHandler(testSnapshot())
	body := `{"id":"r6","badv":["adv.example"],"imp":[{"id":"1","banner":{"w":300,"h":250}}]}`
	w := doBid(t, h, body)
	if w.Code != http.StatusNoContent {
		t.Fatalf("want 204 (badv), got %d", w.Code)
	}
}

func TestBidNonRUBCurrencyIsNoBid(t *testing.T) {
	h := newTestHandler(testSnapshot())
	body := `{"id":"r7","cur":["USD"],"imp":[{"id":"1","banner":{"w":300,"h":250}}]}`
	w := doBid(t, h, body)
	if w.Code != http.StatusNoContent {
		t.Fatalf("want 204 (USD only), got %d", w.Code)
	}
}

func TestEventTokenRoundtrip(t *testing.T) {
	tok, err := SignEventToken("bid-123", time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	if got := VerifyEventToken(tok); got != "bid-123" {
		t.Fatalf("roundtrip failed: %q", got)
	}
	if VerifyEventToken("v1.bid-123.1.deadbeef") != "" {
		t.Fatal("expired/forged token must fail")
	}
}

// BenchmarkBidHotPath измеряет latency/аллокации горячего пути принятия
// решения о ставке (degraded mode без Redis): парсинг запроса, матчинг
// таргетинга, сборка adm с erid, подпись nurl. Служит ориентиром для
// нагрузочного профиля и регрессий производительности в CI.
func BenchmarkBidHotPath(b *testing.B) {
	h := newTestHandler(testSnapshot())
	body := `{"id":"r2","at":1,"imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":10.5}],
		"site":{"domain":"news.example","cat":["IAB12"]},
		"device":{"ua":"Mozilla","os":"android","devicetype":1,"geo":{"country":"RUS"},"ifa":"ifa-1"}}`
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		r := httptest.NewRequest(http.MethodPost, "/rtb/bid/key1", strings.NewReader(body))
		r.SetPathValue("endpointKey", "key1")
		w := httptest.NewRecorder()
		h.Bid(w, r)
		if w.Code != http.StatusOK {
			b.Fatalf("want 200, got %d", w.Code)
		}
	}
}

// Партнёр в статусе testing (онбординг) торгуется — паритет с Python и с
// фильтром фида. Раньше Go отвечал 404, и SSP не мог пройти интеграционный тест.
func TestBidTestingPartnerIsBiddable(t *testing.T) {
	snap := testSnapshot()
	snap.Partners["key1"].Status = "testing"
	h := newTestHandler(snap)
	body := `{"id":"r5","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`
	w := doBid(t, h, body)
	if w.Code != http.StatusOK {
		t.Fatalf("testing partner must bid: want 200, got %d body=%s", w.Code, w.Body.String())
	}
}

func TestBidPausedPartnerIsRejected(t *testing.T) {
	for _, st := range []string{"paused", "blocked", "draft"} {
		snap := testSnapshot()
		snap.Partners["key1"].Status = st
		h := newTestHandler(snap)
		w := doBid(t, h, `{"id":"r6","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`)
		if w.Code != http.StatusNotFound {
			t.Fatalf("status %s: want 404, got %d", st, w.Code)
		}
	}
}

// Ответ содержит lurl с макросами loss-reason / min-to-win, чтобы SSP мог
// сообщить о проигрыше (освобождение бюджета + отчёт «почему проигрываем»).
func TestBidResponseHasLURL(t *testing.T) {
	h := newTestHandler(testSnapshot())
	w := doBid(t, h, `{"id":"r7","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", w.Code)
	}
	var resp BidResponse
	_ = json.Unmarshal(w.Body.Bytes(), &resp)
	bid := resp.SeatBid[0].Bid[0]
	if !strings.Contains(bid.LURL, "/rtb/loss?bid=v1.") ||
		!strings.Contains(bid.LURL, "reason=${AUCTION_LOSS}") ||
		!strings.Contains(bid.LURL, "mtw=${AUCTION_MIN_TO_WIN}") {
		t.Fatalf("lurl malformed: %s", bid.LURL)
	}
}

// test=1 (OpenRTB): бид отдаём, чтобы SSP увидел ответ, но парсинг флага не
// должен ломать запрос. Без Redis резервация и так пропускается — проверяем
// сам путь.
func TestBidTestFlagStillBids(t *testing.T) {
	h := newTestHandler(testSnapshot())
	w := doBid(t, h, `{"id":"r8","test":1,"imp":[{"id":"1","tagid":"slot-1","banner":{"w":300,"h":250}}]}`)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200 on test=1, got %d", w.Code)
	}
}

func TestMacroOrEmpty(t *testing.T) {
	if macroOrEmpty("${AUCTION_LOSS}") != "" {
		t.Fatal("unsubstituted macro must become empty")
	}
	if macroOrEmpty("102") != "102" {
		t.Fatal("real value must pass through")
	}
}

func TestHealthz(t *testing.T) {
	h := newTestHandler(testSnapshot())
	r := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()
	h.Healthz(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), `"status":"ok"`) {
		t.Fatalf("healthz body: %s", w.Body.String())
	}
}
