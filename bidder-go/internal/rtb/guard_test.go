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
	req := httptest.NewRequest(http.MethodPost, "/rtb/bid/key1", strings.NewReader(body))
	req.SetPathValue("endpointKey", "key1")
	rec := httptest.NewRecorder()
	h.Bid(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status=%d want 413", rec.Code)
	}
}

// Тело в пределах лимита обрабатывается как обычно (регрессия на MaxBytesReader).
func TestBidNormalBodyWithinLimit(t *testing.T) {
	h := newTestHandler(testSnapshot())
	body := `{"id":"ok","imp":[{"id":"1","banner":{"w":300,"h":250}}],"cur":["RUB"]}`
	req := httptest.NewRequest(http.MethodPost, "/rtb/bid/key1", strings.NewReader(body))
	req.SetPathValue("endpointKey", "key1")
	rec := httptest.NewRecorder()
	h.Bid(rec, req)
	if rec.Code == http.StatusRequestEntityTooLarge || rec.Code == http.StatusBadRequest {
		t.Fatalf("unexpected status=%d", rec.Code)
	}
}

func TestBidDeadline(t *testing.T) {
	cases := []struct {
		tmax int
		want time.Duration
	}{
		{0, defaultBidBudget},                    // tmax не задан -> дефолт
		{-5, defaultBidBudget},                   // мусор -> дефолт
		{10, minBidBudget},                       // слишком мало -> минимум
		{50, 30 * time.Millisecond},              // 50 - 20 резерв
		{100, defaultBidBudget},                  // 80 -> ровно дефолт
		{1000, defaultBidBudget},                 // большой tmax не раздувает бюджет
	}
	for _, c := range cases {
		if got := bidDeadline(c.tmax); got != c.want {
			t.Errorf("bidDeadline(%d)=%v want %v", c.tmax, got, c.want)
		}
	}
}

// Токен партнёра: сверка по sha256-хэшу, оба заголовка, отсутствие хэша = без проверки.
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
	if !partnerTokenOK(&feed.Partner{}, mk("", "")) {
		t.Fatal("partner without hash must not require token")
	}
}
