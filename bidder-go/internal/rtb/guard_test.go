package rtb

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
)

// Тело больше MaxBidRequestBytes отбивается 413 до парсинга/таргетинга.
func TestBidOversizedBodyIs413(t *testing.T) {
	h := newTestHandler(testSnapshot())
	body := `{"id":"big","imp":[{"id":"1","banner":{"w":300,"h":250}}],"ext":{"pad":"` +
		strings.Repeat("x", MaxBidRequestBytes+1024) + `"}}`
	rec := httptest.NewRecorder()
	h.Bid(rec, bidRequest("key1", body))
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status=%d want 413", rec.Code)
	}
}

// Тело в пределах лимита обрабатывается как обычно (регрессия на MaxBytesReader).
func TestBidNormalBodyWithinLimit(t *testing.T) {
	h := newTestHandler(testSnapshot())
	body := `{"id":"ok","imp":[{"id":"1","banner":{"w":300,"h":250}}],"cur":["RUB"]}`
	rec := httptest.NewRecorder()
	h.Bid(rec, bidRequest("key1", body))
	if rec.Code == http.StatusRequestEntityTooLarge || rec.Code == http.StatusBadRequest {
		t.Fatalf("unexpected status=%d", rec.Code)
	}
}

func TestBidDeadline(t *testing.T) {
	cases := []struct {
		tmax int
		want time.Duration
	}{
		{0, defaultBidBudget},       // tmax не задан -> дефолт
		{-5, defaultBidBudget},      // мусор -> дефолт
		{10, minBidBudget},          // слишком мало -> минимум
		{50, 30 * time.Millisecond}, // 50 - 20 резерв
		{100, defaultBidBudget},     // 80 -> ровно дефолт
		{1000, defaultBidBudget},    // большой tmax не раздувает бюджет
	}
	for _, c := range cases {
		if got := bidDeadline(c.tmax); got != c.want {
			t.Errorf("bidDeadline(%d)=%v want %v", c.tmax, got, c.want)
		}
	}
}

// Токен партнёра: сверка по sha256-хэшу, оба заголовка; отсутствие хэша =
// отказ (audit S-06: fail-closed, паритет с rtb.py «SSP token not issued»).
func TestPartnerTokenOK(t *testing.T) {
	p := &feed.Partner{AuthTokenHash: sha256Hex("secret-token")}

	mk := func(hdr, val string) *http.Request {
		r := httptest.NewRequest(http.MethodPost, "/rtb/bid/x", nil)
		if hdr != "" {
			r.Header.Set(hdr, val)
		}
		return r
	}
	if !partnerTokenOK(p, mk("X-CentralDSP-Token", "secret-token")) {
		t.Fatal("valid token via X-CentralDSP-Token must pass")
	}
	if !partnerTokenOK(p, mk("X-SSP-Token", "secret-token")) {
		t.Fatal("valid token via legacy X-SSP-Token must pass")
	}
	if partnerTokenOK(p, mk("X-CentralDSP-Token", "wrong")) {
		t.Fatal("wrong token must fail")
	}
	if partnerTokenOK(p, mk("", "")) {
		t.Fatal("missing token must fail when hash is set")
	}
	if partnerTokenOK(&feed.Partner{}, mk("", "")) {
		t.Fatal("partner without hash must be rejected (fail-closed)")
	}
	if got := checkPartnerToken(&feed.Partner{}, mk("X-CentralDSP-Token", "any"), time.Now()); got != tokenNoHash {
		t.Fatalf("verdict for tokenless partner: want tokenNoHash, got %v", got)
	}
}

// TRUSTED_PROXIES: список IP/CIDR через запятую; мусор — ошибка старта.
func TestParseTrustedProxies(t *testing.T) {
	nets, err := ParseTrustedProxies(" 127.0.0.1, ::1 ,172.16.0.0/12 ")
	if err != nil || len(nets) != 3 {
		t.Fatalf("want 3 nets, got %d err=%v", len(nets), err)
	}
	if nets, err := ParseTrustedProxies(""); err != nil || len(nets) != 0 {
		t.Fatalf("empty must be no proxies, got %d err=%v", len(nets), err)
	}
	if _, err := ParseTrustedProxies("10.0.0.0/8,garbage"); err == nil {
		t.Fatal("garbage entry must fail")
	}
}
