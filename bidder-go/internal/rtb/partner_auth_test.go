package rtb

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
)

// IP-whitelist партнёра (паритет с rtb.py/ssp_check.ip_allowed): пустой список —
// любой IP; непустой — только перечисленные IP/CIDR. Audit S-06: XFF/X-Real-IP
// учитываются ТОЛЬКО от доверенного прокси (TRUSTED_PROXIES), иначе клиент =
// RemoteAddr — подделать XFF с чужого адреса нельзя.
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
	// Мусор в XFF от доверенного прокси: fallback на RemoteAddr (127.0.0.1 не в
	// whitelist) -> 403, а не fail-open.
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

// X-Real-IP от доверенного прокси учитывается, если XFF нет.
func TestBidXRealIPFromTrustedProxy(t *testing.T) {
	snap := testSnapshot()
	nets, _ := feed.ParseIPWhitelist([]byte(`["203.0.113.7"]`))
	snap.Partners["key1"].IPNets = nets
	h := newTestHandler(snap)
	proxies, _ := ParseTrustedProxies("10.0.0.0/8")
	h.SetTrustedProxies(proxies)
	body := `{"id":"r13","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`

	r := bidRequest("key1", body)
	r.RemoteAddr = "10.2.3.4:9"
	r.Header.Set("X-Real-IP", "203.0.113.7")
	w := httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("x-real-ip from trusted proxy: want 200, got %d", w.Code)
	}
	r = bidRequest("key1", body)
	r.RemoteAddr = "198.51.100.1:9"
	r.Header.Set("X-Real-IP", "203.0.113.7")
	w = httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusForbidden {
		t.Fatalf("x-real-ip from untrusted remote: want 403, got %d", w.Code)
	}
}

// Audit S-06: партнёр без выданного/с отозванным токеном закрыт всегда — даже для
// автопроверки со Skip-Token. Причина в /metrics — token_not_issued.
func TestBidPartnerWithoutTokenIsForbidden(t *testing.T) {
	snap := testSnapshot()
	snap.Partners["key1"].AuthTokenHash = ""
	h := newTestHandler(snap)
	reg := metrics.New()
	h.SetMetrics(reg)
	body := `{"id":"r10","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`

	if w := doBid(t, h, body); w.Code != http.StatusForbidden {
		t.Fatalf("no hash + token: want 403, got %d", w.Code)
	}
	r := bidRequest("key1", body)
	r.Header.Del(TokenHeader)
	w := httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusForbidden {
		t.Fatalf("no hash + no token: want 403, got %d", w.Code)
	}
	h.SetInternalToken("infra-secret")
	r = bidRequest("key1", body)
	r.Header.Del(TokenHeader)
	r.Header.Set(internalCheckHeader, "infra-secret")
	r.Header.Set(skipTokenHeader, "1")
	w = httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusForbidden {
		t.Fatalf("internal skip-token must not open partner without hash: want 403, got %d", w.Code)
	}
	rec := httptest.NewRecorder()
	reg.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if !strings.Contains(rec.Body.String(), `reason="token_not_issued"} 3`) {
		t.Fatalf("want token_not_issued=3 in metrics, got:\n%s", rec.Body.String())
	}
}

// Неверный токен считается отдельно от «не выдан» и от IP-отказа.
func TestBidAuthReasonsAreSeparate(t *testing.T) {
	snap := testSnapshot()
	nets, _ := feed.ParseIPWhitelist([]byte(`["203.0.113.7"]`))
	snap.Partners["key1"].IPNets = nets
	h := newTestHandler(snap)
	reg := metrics.New()
	h.SetMetrics(reg)
	body := `{"id":"r14","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`

	r := bidRequest("key1", body)
	r.Header.Set(TokenHeader, "wrong")
	w := httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusForbidden {
		t.Fatalf("wrong token: want 403, got %d", w.Code)
	}
	r = bidRequest("key1", body)
	r.RemoteAddr = "198.51.100.1:1"
	w = httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusForbidden {
		t.Fatalf("ip denied: want 403, got %d", w.Code)
	}
	rec := httptest.NewRecorder()
	reg.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	out := rec.Body.String()
	for _, want := range []string{`reason="bad_auth"} 1`, `reason="ip_denied"} 1`, `reason="token_not_issued"} 0`} {
		if !strings.Contains(out, want) {
			t.Fatalf("want %s in metrics, got:\n%s", want, out)
		}
	}
}

// Невалидный ip_whitelist в фиде -> fail-closed (403, whitelist_invalid), а не
// «фильтр отключён».
func TestBidInvalidWhitelistFailsClosed(t *testing.T) {
	snap := testSnapshot()
	snap.Partners["key1"].WhitelistInvalid = true
	h := newTestHandler(snap)
	reg := metrics.New()
	h.SetMetrics(reg)
	body := `{"id":"r11","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`
	if w := doBid(t, h, body); w.Code != http.StatusForbidden {
		t.Fatalf("invalid whitelist: want 403, got %d", w.Code)
	}
	rec := httptest.NewRecorder()
	reg.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	if !strings.Contains(rec.Body.String(), `reason="whitelist_invalid"} 1`) {
		t.Fatalf("want whitelist_invalid=1 in metrics, got:\n%s", rec.Body.String())
	}
}

// Окно ротации: предыдущий токен принимается до token_prev_valid_until, после — нет.
func TestBidTokenRotationGrace(t *testing.T) {
	snap := testSnapshot()
	p := snap.Partners["key1"]
	p.AuthTokenHash = sha256Hex("new-token")
	p.TokenPrevHash = sha256Hex(testPartnerToken)
	until := time.Now().Add(10 * time.Minute)
	p.TokenPrevValidUntil = &until
	h := newTestHandler(snap)
	body := `{"id":"r12","imp":[{"id":"1","banner":{"w":300,"h":250}}]}`

	if w := doBid(t, h, body); w.Code != http.StatusOK {
		t.Fatalf("previous token inside grace window: want 200, got %d", w.Code)
	}
	r := bidRequest("key1", body)
	r.Header.Set(TokenHeader, "new-token")
	w := httptest.NewRecorder()
	h.Bid(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("new token: want 200, got %d", w.Code)
	}
	expired := time.Now().Add(-time.Second)
	p.TokenPrevValidUntil = &expired
	if w := doBid(t, h, body); w.Code != http.StatusForbidden {
		t.Fatalf("previous token after grace window: want 403, got %d", w.Code)
	}
}

// Фид: мусор в ip_whitelist помечает партнёра WhitelistInvalid (fail-closed),
// валидный список парсится, пустой/null — nil без ошибки.
func TestParseIPWhitelistStrict(t *testing.T) {
	if nets, err := feed.ParseIPWhitelist(nil); err != nil || nets != nil {
		t.Fatalf("empty: want nil,nil got %v,%v", nets, err)
	}
	if nets, err := feed.ParseIPWhitelist([]byte(`null`)); err != nil || nets != nil {
		t.Fatalf("null: want nil,nil got %v,%v", nets, err)
	}
	if nets, err := feed.ParseIPWhitelist([]byte(`["203.0.113.7","2001:db8::/32",""]`)); err != nil || len(nets) != 2 {
		t.Fatalf("valid: want 2 nets, got %v,%v", nets, err)
	}
	if _, err := feed.ParseIPWhitelist([]byte(`["203.0.113.7","garbage"]`)); err == nil {
		t.Fatal("garbage entry must be an error")
	}
	if _, err := feed.ParseIPWhitelist([]byte(`{"not":"array"}`)); err == nil {
		t.Fatal("non-array must be an error")
	}
}
