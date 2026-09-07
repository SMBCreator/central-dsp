package rtb

// Audit R-02: bidmeta + событие bid — обязательный bookkeeping. Если Redis не
// принял запись, бид НЕ отдаётся (иначе платный показ пройдёт без списания и
// без учёта — backend не найдёт bidmeta).

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/redisx"
)

const bookkeepingBidReq = `{"id":"r1","test":1,"imp":[{"id":"1","banner":{"w":300,"h":250},"bidfloor":1}],` +
	`"device":{"ifa":"dev-1","ua":"Mozilla/5.0"},"site":{"domain":"pub.example"}}`

func metricValue(t *testing.T, reg *metrics.Registry, name string) string {
	t.Helper()
	w := httptest.NewRecorder()
	reg.Handler().ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	for _, line := range strings.Split(w.Body.String(), "\n") {
		if strings.HasPrefix(line, name+" ") {
			return strings.TrimSpace(strings.TrimPrefix(line, name+" "))
		}
	}
	return ""
}

func TestBid_BookkeepingWritesBidMeta(t *testing.T) {
	h, mr := newMiniRedisBidHandler(t, testSnapshot())
	w := doBid(t, h, bookkeepingBidReq)
	if w.Code != http.StatusOK {
		t.Fatalf("want 200 bid, got %d: %s", w.Code, w.Body.String())
	}
	keys := mr.Keys()
	var hasMeta bool
	for _, k := range keys {
		if strings.HasPrefix(k, "bidmeta:") {
			hasMeta = true
		}
	}
	if !hasMeta {
		t.Fatalf("bidmeta must be written before the bid is returned; keys=%v", keys)
	}
}

func TestBid_NoBidWhenBookkeepingFails(t *testing.T) {
	h, mr := newMiniRedisBidHandler(t, testSnapshot())
	reg := metrics.New()
	h.SetMetrics(reg)

	// Redis умирает после старта: gate пропускает (test=1, fail-open), но
	// persistBidMeta падает -> 204 no-bid, счётчик bookkeeping_errors растёт.
	mr.Close()
	w := doBid(t, h, bookkeepingBidReq)
	if w.Code != http.StatusNoContent {
		t.Fatalf("redis down: want 204 no-bid, got %d: %s", w.Code, w.Body.String())
	}
	if got := metricValue(t, reg, "bidder_bookkeeping_errors_total"); got != "1" {
		t.Fatalf("bidder_bookkeeping_errors_total: want 1, got %q", got)
	}
}

// Реальный (test=0) бид: гейт зарезервировал деньги, bidmeta записался, но
// XADD события bid упал (rtb:events занят ключом другого типа -> WRONGTYPE).
// Ожидание: no-bid, резерв возвращён тем же Lua, что loss/expired,
// bidstate = aborted, pending:bids пуст. Без этого деньги висели бы в
// spend:camp до sweeper-а, а SSP получил бы бид без учёта.
func TestBid_BookkeepingFailureReleasesReservation(t *testing.T) {
	h, mr := newMiniRedisBidHandler(t, testSnapshot())
	reg := metrics.New()
	h.SetMetrics(reg)
	mr.Set("rtb:events", "not-a-stream")

	realBid := strings.Replace(bookkeepingBidReq, `"test":1,`, "", 1)
	w := doBid(t, h, realBid)
	if w.Code != http.StatusNoContent {
		t.Fatalf("xadd failed: want 204 no-bid, got %d: %s", w.Code, w.Body.String())
	}
	if got := spendOf(t, mr, redisx.SpendKey("camp", "1", today)); got > 1e-9 || got < -1e-9 {
		t.Fatalf("spend:camp must be refunded after aborted bid, got %v", got)
	}
	if members, _ := mr.ZMembers(redisx.PendingBidsKey); len(members) != 0 {
		t.Fatalf("pending:bids must be empty after abort, got %v", members)
	}
	var aborted bool
	for _, k := range mr.Keys() {
		if strings.HasPrefix(k, "bidstate:") {
			if st, _ := mr.Get(k); st == "aborted" {
				aborted = true
			}
		}
	}
	if !aborted {
		t.Fatalf("bidstate:{bid} must be 'aborted'; keys=%v", mr.Keys())
	}
	if got := metricValue(t, reg, "bidder_bookkeeping_errors_total"); got != "1" {
		t.Fatalf("bidder_bookkeeping_errors_total: want 1, got %q", got)
	}

	// Redis починили — тот же запрос снова торгуется и резервирует.
	mr.Del("rtb:events")
	w = doBid(t, h, realBid)
	if w.Code != http.StatusOK {
		t.Fatalf("after recovery: want 200, got %d: %s", w.Code, w.Body.String())
	}
	if got := spendOf(t, mr, redisx.SpendKey("camp", "1", today)); got <= 0 {
		t.Fatalf("spend:camp must hold the new reservation, got %v", got)
	}
}
