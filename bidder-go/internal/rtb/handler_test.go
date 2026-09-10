package rtb

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
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
			// Audit S-06: партнёр без auth_token_hash закрыт (403), поэтому у
			// тестового партнёра токен выдан; см. testPartnerToken / bidRequest.
			"key1": {ID: "5", EndpointKey: "key1", Status: "active", BidFloor: 10,
				AuthTokenHash: sha256Hex(testPartnerToken)},
		},
		GBlack:   map[string]map[string]bool{},
		GWhite:   map[string]map[string]bool{},
		LoadedAt: time.Now(),
	}
}

// testPartnerToken — открытый SSP-токен партнёра key1 из testSnapshot.
const testPartnerToken = "test-ssp-token"

// bidRequest — POST /rtb/bid/{key} с валидным токеном партнёра key1.
func bidRequest(key, body string) *http.Request {
	r := httptest.NewRequest(http.MethodPost, "/rtb/bid/"+key, strings.NewReader(body))
	r.SetPathValue("endpointKey", key)
	r.Header.Set(TokenHeader, testPartnerToken)
	return r
}

func bidRequestWithToken(key, body, token string) *http.Request {
	r := bidRequest(key, body)
	r.Header.Set(TokenHeader, token)
	return r
}

func doBid(t *testing.T, h *Handler, body string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	h.Bid(w, bidRequest("key1", body))
	return w
}

func TestBidFeedNotLoaded503(t *testing.T) {
	// Audit R-02: до первой успешной загрузки фида биддер не знает партнёров и
	// кампаний -> 503 feed_not_loaded (а не 404 «партнёр не найден»), чтобы
	// балансировщик не считал инстанс здоровым.
	h := newTestHandler(nil)
	w := doBid(t, h, `{"id":"r1","imp":[{"id":"1"}]}`)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("want 503, got %d", w.Code)
	}
	hw := httptest.NewRecorder()
	h.Healthz(hw, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if hw.Code != http.StatusServiceUnavailable {
		t.Fatalf("healthz: want 503, got %d", hw.Code)
	}
	var body map[string]any
	_ = json.Unmarshal(hw.Body.Bytes(), &body)
	if body["status"] != "feed_not_loaded" {
		t.Fatalf("healthz status: want feed_not_loaded, got %v", body["status"])
	}
}

func TestBidUnknownEndpoint404(t *testing.T) {
	// Фид загружен, но такого endpointKey нет -> 404 (паритет с Python).
	h := newTestHandler(testSnapshot())
	w := httptest.NewRecorder()
	h.Bid(w, bidRequest("no-such-key", `{"id":"r1","imp":[{"id":"1"}]}`))
	if w.Code != http.StatusNotFound {
		t.Fatalf("want 404, got %d", w.Code)
	}
}

func TestBidStaleFeed503(t *testing.T) {
	// Audit R-02: снапшот старше FEED_MAX_AGE -> no-bid feed_stale + /healthz 503.
	// Иначе биддер торговал бы по бюджетам/статусам возрастом в часы.
	store := feed.NewStore(feed.Config{BackendURL: "http://localhost:0", MaxAge: time.Minute})
	snap := testSnapshot()
	snap.LoadedAt = time.Now().Add(-2 * time.Minute)
	store.SetSnapshot(snap)
	h := NewHandler(store, nil)

	w := doBid(t, h, `{"id":"r1","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`)
	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("stale feed: want 503, got %d", w.Code)
	}
	hw := httptest.NewRecorder()
	h.Healthz(hw, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if hw.Code != http.StatusServiceUnavailable {
		t.Fatalf("healthz: want 503, got %d", hw.Code)
	}
	var body map[string]any
	_ = json.Unmarshal(hw.Body.Bytes(), &body)
	if body["status"] != "feed_stale" {
		t.Fatalf("healthz status: want feed_stale, got %v", body["status"])
	}

	// Свежий снапшот возвращает торговлю и 200 на /healthz.
	store.SetSnapshot(testSnapshot())
	hw = httptest.NewRecorder()
	h.Healthz(hw, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if hw.Code != http.StatusOK {
		t.Fatalf("healthz after refresh: want 200, got %d", hw.Code)
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
	if !strings.Contains(bid.BURL, "/api/rtb/burl?bid=v1.") || !strings.Contains(bid.BURL, "price=${AUCTION_PRICE}") {
		t.Fatalf("burl missing/unsigned: %s", bid.BURL)
	}
	if nt, bt := bid.NURL[strings.Index(bid.NURL, "bid="):], bid.BURL[strings.Index(bid.BURL, "bid="):]; nt != bt {
		t.Fatalf("nurl and burl must carry the same event token: %s vs %s", nt, bt)
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
		w := httptest.NewRecorder()
		h.Bid(w, bidRequest("key1", body))
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

func TestFeedReloadAuth(t *testing.T) {
	h := newTestHandler(testSnapshot())
	// Токен не настроен -> 404 (эндпоинта «нет»).
	w := httptest.NewRecorder()
	h.FeedReload(w, httptest.NewRequest(http.MethodPost, "/internal/feed/reload", nil))
	if w.Code != http.StatusNotFound {
		t.Fatalf("no token configured: want 404, got %d", w.Code)
	}
	h.SetInternalToken("s")
	w = httptest.NewRecorder()
	h.FeedReload(w, httptest.NewRequest(http.MethodPost, "/internal/feed/reload", nil))
	if w.Code != http.StatusForbidden {
		t.Fatalf("missing header: want 403, got %d", w.Code)
	}
}

func TestFeedReloadReportsRefreshFailure(t *testing.T) {
	// Audit R-02: backend недоступен -> reload не маскирует ошибку «ok: true»;
	// backend (ssp_check) по этому ответу понимает, что кампания не в снапшоте.
	h := newTestHandler(testSnapshot()) // BackendURL localhost:0 -> connection refused
	h.SetInternalToken("s")
	r := httptest.NewRequest(http.MethodPost, "/internal/feed/reload", nil)
	r.Header.Set(internalCheckHeader, "s")
	w := httptest.NewRecorder()
	h.FeedReload(w, r)
	if w.Code != http.StatusBadGateway {
		t.Fatalf("want 502, got %d: %s", w.Code, w.Body.String())
	}
	var body map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	if body["ok"] != false || body["error"] == nil || body["error"] == "" {
		t.Fatalf("want ok=false with error, got %v", body)
	}
}

func TestStaleFeedCountsNoBidReason(t *testing.T) {
	store := feed.NewStore(feed.Config{BackendURL: "http://localhost:0", MaxAge: time.Minute})
	snap := testSnapshot()
	snap.LoadedAt = time.Now().Add(-2 * time.Minute)
	store.SetSnapshot(snap)
	h := NewHandler(store, nil)
	reg := metrics.New()
	h.SetMetrics(reg)
	doBid(t, h, `{"id":"r1","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`)
	rec := httptest.NewRecorder()
	reg.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if !strings.Contains(rec.Body.String(), `bidder_no_bid_total{reason="feed_stale"} 1`) {
		t.Fatalf("expected feed_stale no-bid reason, got:\n%s", rec.Body.String())
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
