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

// IP-whitelist партнёра (паритет с rtb.py/ssp_check.ip_allowed): пустой список —
// любой IP; непустой — только перечисленные IP/CIDR. X-Forwarded-For учитывается
// ТОЛЬКО от доверенного прокси (audit S-06) — иначе whitelist обходится одним
// заголовком.
func TestBidIPWhitelist(t *testing.T) {
	snap := testSnapshot()
	nets, err := feed.ParseIPWhitelist([]byte(`["203.0.113.7","10.1.0.0/16"]`))
	if err != nil {
		t.Fatal(err)
	}
	snap.Partners["key1"].IPNets = nets
	h := newTestHandler(snap)
	proxies, err := ParseTrustedProxies("127.0.0.1")
	if err != nil {
		t.Fatal(err)
	}
	h.SetTrustedProxies(proxies)
	body := `{"id":"r9","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`

	mk := func(remote, xff string) *httptest.ResponseRecorder {
		r := bidRequest("key1", body)
		r.RemoteAddr = remote
		if xff != "" {
			r.Header.Set("X-Forwarded-For", xff)
		}
		w := httptest.NewRecorder()
		h.Bid(w, r)
		return w
	}
	if w := mk("203.0.113.7:5555", ""); w.Code != http.StatusOK {
		t.Fatalf("exact ip: want 200, got %d", w.Code)
	}
	if w := mk("10.1.2.3:5555", ""); w.Code != http.StatusOK {
		t.Fatalf("cidr: want 200, got %d", w.Code)
	}
	if w := mk("198.51.100.1:5555", ""); w.Code != http.StatusForbidden {
		t.Fatalf("foreign ip: want 403, got %d", w.Code)
	}
	// За Caddy (доверенный 127.0.0.1): реальный клиент — первый адрес XFF.
	if w := mk("127.0.0.1:1", "203.0.113.7, 127.0.0.1"); w.Code != http.StatusOK {
		t.Fatalf("xff allowed: want 200, got %d", w.Code)
	}
	if w := mk("127.0.0.1:1", "198.51.100.1"); w.Code != http.StatusForbidden {
		t.Fatalf("xff foreign: want 403, got %d", w.Code)
	}
	// Чужой хост подставляет XFF с разрешённым IP — заголовок игнорируется,
	// клиент = RemoteAddr -> 403. Это и есть закрытая дыра S-06.
	if w := mk("198.51.100.1:5555", "203.0.113.7"); w.Code != http.StatusForbidden {
		t.Fatalf("spoofed xff from untrusted remote must be denied: want 403, got %d", w.Code)
	}
	// Мусор в XFF от доверенного прокси = клиент не определён -> 403, а не fail-open.
	if w := mk("127.0.0.1:1", "not-an-ip"); w.Code != http.StatusForbidden {
		t.Fatalf("garbage xff: want 403, got %d", w.Code)
	}
	// Без доверенных прокси вообще XFF никогда не учитывается.
	h.SetTrustedProxies(nil)
	if w := mk("127.0.0.1:1", "203.0.113.7"); w.Code != http.StatusForbidden {
		t.Fatalf("no trusted proxies: xff must be ignored, want 403, got %d", w.Code)
	}
	h.SetTrustedProxies(proxies)

	// Автопроверка из кабинета: internal-токен обходит whitelist; без токена — нет.
	h.SetInternalToken("infra-secret")
	r := bidRequest("key1", body)
	r.RemoteAddr = "172.18.0.5:4000"
	r.Header.Set(internalCheckHeader, "infra-secret")
	w := httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("internal check bypass: want 200, got %d", w.Code)
	}
	r.Header.Set(internalCheckHeader, "wrong")
	w = httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusForbidden {
		t.Fatalf("wrong internal token must not bypass: want 403, got %d", w.Code)
	}
}

// Audit S-06: партнёр без выданного токена закрыт всегда, даже для internal-
// проверки со Skip-Token; невалидный whitelist -> fail-closed; предыдущий
// токен принимается только в окне ротации; причины отказа считаются раздельно.
func TestBidTokenLifecycle(t *testing.T) {
	body := `{"id":"r10","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`

	t.Run("no hash is 403 even for internal check", func(t *testing.T) {
		snap := testSnapshot()
		snap.Partners["key1"].AuthTokenHash = ""
		h := newTestHandler(snap)
		h.SetInternalToken("infra-secret")
		if w := doBid(t, h, body); w.Code != http.StatusForbidden {
			t.Fatalf("partner w/o token: want 403, got %d", w.Code)
		}
		r := bidRequest("key1", body)
		r.Header.Del(TokenHeader)
		w := httptest.NewRecorder()
		h.Bid(w, r)
		if w.Code != http.StatusForbidden {
			t.Fatalf("no hash + no token: want 403, got %d", w.Code)
		}
		r = bidRequest("key1", body)
		r.Header.Del(TokenHeader)
		r.Header.Set(internalCheckHeader, "infra-secret")
		r.Header.Set(skipTokenHeader, "1")
		w = httptest.NewRecorder()
		h.Bid(w, r)
		if w.Code != http.StatusForbidden {
			t.Fatalf("internal skip-token on tokenless partner: want 403, got %d", w.Code)
		}
	})

	t.Run("wrong token 403, missing token 403", func(t *testing.T) {
		h := newTestHandler(testSnapshot())
		w := httptest.NewRecorder()
		h.Bid(w, bidRequestWithToken("key1", body, "wrong"))
		if w.Code != http.StatusForbidden {
			t.Fatalf("wrong token: want 403, got %d", w.Code)
		}
		r := bidRequest("key1", body)
		r.Header.Del(TokenHeader)
		w = httptest.NewRecorder()
		h.Bid(w, r)
		if w.Code != http.StatusForbidden {
			t.Fatalf("missing token: want 403, got %d", w.Code)
		}
	})

	t.Run("internal skip-token still works for partner with hash", func(t *testing.T) {
		h := newTestHandler(testSnapshot())
		h.SetInternalToken("infra-secret")
		r := bidRequest("key1", body)
		r.Header.Del(TokenHeader)
		r.Header.Set(internalCheckHeader, "infra-secret")
		r.Header.Set(skipTokenHeader, "1")
		w := httptest.NewRecorder()
		h.Bid(w, r)
		if w.Code != http.StatusOK {
			t.Fatalf("ssp_check auto-test path: want 200, got %d", w.Code)
		}
	})

	t.Run("previous token accepted only inside grace window", func(t *testing.T) {
		snap := testSnapshot()
		p := snap.Partners["key1"]
		p.TokenPrevHash = sha256Hex("old-token")
		until := time.Now().Add(10 * time.Minute)
		p.TokenPrevValidUntil = &until
		h := newTestHandler(snap)
		w := httptest.NewRecorder()
		h.Bid(w, bidRequestWithToken("key1", body, "old-token"))
		if w.Code != http.StatusOK {
			t.Fatalf("prev token in window: want 200, got %d", w.Code)
		}
		expired := time.Now().Add(-time.Second)
		p.TokenPrevValidUntil = &expired
		w = httptest.NewRecorder()
		h.Bid(w, bidRequestWithToken("key1", body, "old-token"))
		if w.Code != http.StatusForbidden {
			t.Fatalf("prev token after window: want 403, got %d", w.Code)
		}
		// Текущий токен работает независимо от окна.
		if w := doBid(t, h, body); w.Code != http.StatusOK {
			t.Fatalf("current token: want 200, got %d", w.Code)
		}
	})

	t.Run("invalid whitelist is fail-closed", func(t *testing.T) {
		snap := testSnapshot()
		snap.Partners["key1"].WhitelistInvalid = true
		h := newTestHandler(snap)
		if w := doBid(t, h, body); w.Code != http.StatusForbidden {
			t.Fatalf("invalid whitelist: want 403, got %d", w.Code)
		}
	})

	// Причины отказа на auth-фазе считаются раздельно — иначе алерт «SSP шлёт
	// неверный токен» не отличить от «токен отозван» и «режем по IP».
	t.Run("auth rejects are counted by distinct reasons", func(t *testing.T) {
		snap := testSnapshot()
		nets, err := feed.ParseIPWhitelist([]byte(`["203.0.113.7"]`))
		if err != nil {
			t.Fatal(err)
		}
		snap.Partners["key1"].IPNets = nets
		h := newTestHandler(snap)
		reg := metrics.New()
		h.SetMetrics(reg)

		w := httptest.NewRecorder()
		r := bidRequestWithToken("key1", body, "wrong")
		r.RemoteAddr = "203.0.113.7:1"
		h.Bid(w, r) // bad_auth
		w = httptest.NewRecorder()
		r = bidRequest("key1", body)
		r.RemoteAddr = "198.51.100.1:1"
		h.Bid(w, r) // ip_denied
		snap.Partners["key1"].AuthTokenHash = ""
		w = httptest.NewRecorder()
		h.Bid(w, bidRequest("key1", body)) // token_not_issued
		snap.Partners["key1"].AuthTokenHash = sha256Hex(testPartnerToken)
		snap.Partners["key1"].WhitelistInvalid = true
		w = httptest.NewRecorder()
		r = bidRequest("key1", body)
		r.RemoteAddr = "203.0.113.7:1"
		h.Bid(w, r) // whitelist_invalid

		rec := httptest.NewRecorder()
		reg.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
		out := rec.Body.String()
		for _, want := range []string{
			`bidder_no_bid_total{reason="bad_auth"} 1`,
			`bidder_no_bid_total{reason="ip_denied"} 1`,
			`bidder_no_bid_total{reason="token_not_issued"} 1`,
			`bidder_no_bid_total{reason="whitelist_invalid"} 1`,
		} {
			if !strings.Contains(out, want) {
				t.Errorf("metrics missing %q\n%s", want, out)
			}
		}
	})
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
